from flask import request, jsonify, Response, current_app
from infrastructure.database.models import db, User, FriendStatus, UserOutfit, ConstellationFriendPage, Friendship
import json
import logging
import time

logger = logging.getLogger(__name__)

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_friend_statues():
    """获取好友状态和星座好友页面数据 - 最终修复版本"""
    
    # 处理 CORS 预检请求
    if request.method == 'OPTIONS':
        response = Response()
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
        response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
        return response
    
    try:
        # ========== 1. 请求验证和解析 ==========
        
        raw_data = request.get_data(as_text=True)
        logger.info(f"Raw request data: {raw_data}")
        
        if not raw_data or raw_data.strip() == '':
            logger.error("Empty request body")
            return jsonify({
                "result": "error",
                "message": "Empty request body",
                "code": "EMPTY_BODY"
            }), 400
        
        # 解析 JSON
        try:
            request_data = json.loads(raw_data)
            logger.info(f"Parsed JSON data: {request_data}")
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            return jsonify({
                "result": "error",
                "message": "Invalid JSON format",
                "code": "INVALID_JSON",
                "details": str(e)
            }), 400
        
        # ========== 2. 验证必需字段 ==========
        
        # 获取 user_id (支持 user 和 user_id 字段)
        user_id = request_data.get('user') or request_data.get('user_id')
        session_token = request_data.get('session')
        
        if not user_id:
            return jsonify({
                "result": "error",
                "message": "Missing 'user' or 'user_id' field",
                "code": "MISSING_USER"
            }), 400
        
        if not session_token:
            return jsonify({
                "result": "error",
                "message": "Missing 'session' field",
                "code": "MISSING_SESSION"
            }), 400
        
        max_friends = request_data.get('max', 150)
        sort_version = request_data.get('sort_ver', 1)
        
        logger.info(f"Processing request for user: {user_id}, max: {max_friends}")
        
        # ========== 3. 验证用户会话 ==========
        
        user = get_cached_user(user_id, session_token)
        if not user:
            logger.warning(f"Invalid session for user: {user_id}")
            return jsonify({
                "result": "error",
                "message": "Invalid session or user not found",
                "code": "INVALID_SESSION",
                "user_id": user_id
            }), 401
        
        # ========== 3.5 自动点亮/补建好友关系 ==========
        # 客户端"点亮"玩家不会调用任何会写 friendship 表的接口(见 friend_helper 的
        # 详细说明), 于是好友树/星座永远空白。这里在拉好友树时按"同关卡的在线玩家"补,
        # 让点亮结果在服务器侧真正成立(默认点亮)。
        try:
            import business.helpers.friend_helper as friend_helper
            friend_helper.sync_lit_relationships(user_id, [])
        except Exception as e:
            logger.warning(f"自动补建好友关系失败: {e}")
            db.session.rollback()

        # ========== 4. 查询好友数据 ==========

        try:
            friend_statuses = FriendStatus.query.filter_by(
                user_id=user_id
            ).order_by(
                FriendStatus.when_created.desc()
            ).limit(max_friends).all()

            # 主控删掉的好友(墓碑行 status='removed', 见 friend_helper.set_friendship_state)
            # 不参与好友树/星座: 这条查询只按 user_id 过滤, 不过滤的话删掉的那个人的
            # abilities/hints 还会被推过去, 客户端本地的树节点又长回来了。
            try:
                removed_ids = {row.friend_id for row in Friendship.query.filter_by(
                    user_id=user_id, status='removed').all()}
                removed_ids |= {row.user_id for row in Friendship.query.filter_by(
                    friend_id=user_id, status='removed').all()}
            except Exception as e:
                logger.warning(f"get_friend_statues: 查询 removed_ids 失败: {e}")
                removed_ids = set()

            # 拉黑标志: 批量查一次(这个接口也是 200~300ms 轮询一次的, 别给每个好友
            # 各打两次 SELECT)。见 friend_helper.block_flags / sub_7C0890 line 1336396。
            try:
                blocked_ids = {row.friend_id for row in Friendship.query.filter_by(
                    user_id=user_id, status='blocked').all()}
            except Exception as e:
                logger.warning(f"get_friend_statues: 查询 blocked_ids 失败: {e}")
                blocked_ids = set()
            try:
                local_blocked_ids = {row.user_id for row in Friendship.query.filter_by(
                    friend_id=user_id, status='blocked').all()}
            except Exception as e:
                logger.warning(f"get_friend_statues: 查询 local_blocked_ids 失败: {e}")
                local_blocked_ids = set()

            # **只回"真的加过好友"的那批**(见 friend_helper.is_added_friend):
            # "点亮"登记出来的行(abilities=[] 且没换过蜡烛)是陌生关系, 不能当好友下发 ——
            # 以前这里只按 user_id 捞 FriendStatus, 于是客户端把刚点亮的陌生人画成好友,
            # 还给一个"设置备注"按钮(点下去就是 set_friend_name)。用户实测 2026-10-05:
            # "陌生人状态下显示设备名" / "为什么陌生人状态下会出现设置备注的按钮"。
            # 拉黑相关的条目例外保留(拉黑标志要能显示出来), 主控删掉的墓碑行照旧排除。
            friend_statuses = [
                fs for fs in friend_statuses
                if fs.friend_id not in removed_ids
                and (friend_helper.is_added_friend(fs)
                     or fs.friend_id in blocked_ids
                     or fs.friend_id in local_blocked_ids)
            ]
        except Exception as e:
            logger.error(f"get_friend_statues: 好友过滤失败: {e}", exc_info=True)
            friend_statuses = []
            
        try:
            # --- 处理块: 单独一个 try, 异常回 500(下面的 except 与它配对) ---
            if not friend_statuses:
                logger.info(f"No friends found for user: {user_id}")
                empty_response = {
                    "set_friend_statues": [],
                    "constellation_friend_pages": [],
                    "result": "success",
                    "user_id": user_id,
                    "total_friends": 0
                }

                response = Response(
                    response=json.dumps(empty_response, ensure_ascii=False),
                    status=200,
                    content_type='application/json; charset=utf-8'
                )
                response.headers.add('Access-Control-Allow-Origin', '*')
                return response

            # 提取好友ID
            friend_ids = [fs.friend_id for fs in friend_statuses]
            logger.info(f"Found {len(friend_ids)} friends for user: {user_id}")

            # ========== 5. 查询装扮数据 ==========

            outfits = {}
            if friend_ids:
                try:
                    outfit_records = UserOutfit.query.filter(
                        UserOutfit.user_id.in_(friend_ids)
                    ).all()
                    outfits = {outfit.user_id: outfit for outfit in outfit_records}
                    logger.info(f"Loaded {len(outfits)} outfits")
                except Exception as e:
                    logger.error(f"Error loading outfits: {e}")
                    outfits = {}

            # ========== 6. 查询星座页面数据 ==========

            constellation_pages = []
            try:
                constellation_pages = ConstellationFriendPage.query.filter(
                    ConstellationFriendPage.user_id == user_id,
                    ConstellationFriendPage.friend_id.in_(friend_ids)
                ).order_by(
                    ConstellationFriendPage.page_index,
                    ConstellationFriendPage.constellation_node_index
                ).all()

                logger.info(f"Loaded {len(constellation_pages)} constellation page entries")
            except Exception as e:
                logger.error(f"Error loading constellation pages: {e}")
                constellation_pages = []

            # ========== 7. 转换好友数据 ==========

            # 拉黑标志在上面(好友过滤那一步)已经查好了: blocked_ids / local_blocked_ids。
            # 见 friend_helper.block_flags / sub_7C0890 line 1336396。

            friend_statues_response = []

            for fs in friend_statuses:
                try:
                    outfit_data = outfits.get(fs.friend_id)

                    friend_response = {
                        "friend_id": str(fs.friend_id),  # 确保是字符串
                        "recvd": int(fs.recvd) if fs.recvd is not None else 0,
                        "nickname": str(fs.nickname) if fs.nickname else "",
                        "when_created": int(fs.when_created) if fs.when_created else 0,
                        "level": int(fs.level) if fs.level else 1,
                        "abilities": []
                    }

                    # 解析 abilities（安全处理）
                    if fs.abilities:
                        try:
                            abilities = json.loads(fs.abilities)
                            if isinstance(abilities, list):
                                friend_response["abilities"] = [int(a) for a in abilities if isinstance(a, (int, float, str))]
                        except Exception as e:
                            logger.warning(f"Error parsing abilities for friend {fs.friend_id}: {e}")
                            friend_response["abilities"] = []

                    # 解析 hints —— 客户端的 friend 解析器(反编译 sub_7C0890)会读
                    # "hints" 这个 int 数组(和 "abilities" 一样按数组解析)。以前这个
                    # 接口只回 abilities 不回 hints, 于是另一端的 get_friend_statues
                    # 轮询里永远拿不到对方那份 hint, 好友树两侧的"已解锁/待确认"状态
                    # 就对不上(实测 "一方已经解锁另一方没有解锁")。这里补齐 hints,
                    # 并顺带补上官方 give_candle.update_friends 那份里另外几个客户端会
                    # 解析、以前没回的字段(created_at / last_seen_outfit / soft_deleted /
                    # they_soft_deleted / unlinked_star_sku)。
                    try:
                        import business.helpers.friend_helper as friend_helper
                        friend_response["hints"] = friend_helper.parse_int_list(fs.hints)
                        friend_response["created_at"] = friend_helper.created_at_iso(fs.when_created)
                    except Exception as e:
                        logger.warning(f"Error building hints for friend {fs.friend_id}: {e}")
                        friend_response["hints"] = []
                        friend_response["created_at"] = None
                    friend_response["last_seen_outfit"] = None
                    friend_response["soft_deleted"] = None
                    friend_response["they_soft_deleted"] = None
                    friend_response["unlinked_star_sku"] = ""

                    # 拉黑标志(客户端 +691 blocked / +692 local_blocked, 见 friend_helper
                    # 的 block_flags 与 sub_7C0890 line 1336396)。缺键客户端按 0 处理,
                    # 好友树照建 —— 就是"拉黑后对方显示没有拉黑"。
                    friend_response["blocked"] = fs.friend_id in blocked_ids
                    friend_response["local_blocked"] = fs.friend_id in local_blocked_ids
                    # 官方这份条目里有 local_soft_deleted(bool), 以前漏了 ——
                    # 0.33.7 的条目解析器会读它(和 soft_deleted 一起决定"这段关系
                    # 还在不在"), 缺键客户端按 false 处理, 但主控删好友后由
                    # set_friendship_state 置的 local_soft_deleted=True 就传不过去了。
                    friend_response["local_soft_deleted"] = bool(
                        getattr(fs, "local_soft_deleted", False))

                    # 构建 outfit 响应
                    if outfit_data:
                        try:
                            # 使用一个安全的字典构建 outfit
                            outfit_response = {}

                            outfit_fields = {
                                "arms": getattr(outfit_data, 'arms_id', 0),
                                "attitude": str(getattr(outfit_data, 'attitude', '0')),
                                "body": getattr(outfit_data, 'body_id', 0),
                                "face": getattr(outfit_data, 'face_id', 0),
                                "feet": getattr(outfit_data, 'feet_id', 0),
                                "hair": getattr(outfit_data, 'hair_id', 0),
                                "hat": getattr(outfit_data, 'hat_id', 0),
                                "height": float(getattr(outfit_data, 'height', 1.0)),
                                "horn": getattr(outfit_data, 'horn_id', 0),
                                "mask": getattr(outfit_data, 'mask_id', 0),
                                "neck": getattr(outfit_data, 'neck_id', 0),
                                "prop": getattr(outfit_data, 'prop_id', 0),
                                "refreshversion": int(getattr(outfit_data, 'refreshversion', 0)),
                                "scale": float(getattr(outfit_data, 'scale', 0.0)),
                                "seed": int(getattr(outfit_data, 'seed', 0)),
                                "voice": int(getattr(outfit_data, 'voice', 0)),
                                "wing": getattr(outfit_data, 'wing_id', 0)
                            }

                            formatted_outfit = {
                                "arms": {"id": int(outfit_fields["arms"]), "mask": 0, "pat": 0, "tex": 0},
                                "attitude": outfit_fields["attitude"],
                                "body": {"id": int(outfit_fields["body"]), "mask": 0, "pat": 0, "tex": 0},
                                "face": {"id": int(outfit_fields["face"]), "mask": 0, "pat": 0, "tex": 0},
                                "feet": {"id": int(outfit_fields["feet"]), "mask": 0, "pat": 0, "tex": 0},
                                "hair": {"id": int(outfit_fields["hair"]), "mask": 0, "pat": 0, "tex": 0},
                                "hat": {"id": int(outfit_fields["hat"]), "mask": 0, "pat": 0, "tex": 0},
                                "height": outfit_fields["height"],
                                "horn": {"id": int(outfit_fields["horn"]), "mask": 0, "pat": 0, "tex": 0},
                                "mask": {"id": int(outfit_fields["mask"]), "mask": 0, "pat": 0, "tex": 0},
                                "neck": {"id": int(outfit_fields["neck"]), "mask": 0, "pat": 0, "tex": 0},
                                "prop": {"id": int(outfit_fields["prop"]), "mask": 0, "pat": 0, "tex": 0},
                                "refreshversion": outfit_fields["refreshversion"],
                                "scale": outfit_fields["scale"],
                                "seed": outfit_fields["seed"],
                                "voice": outfit_fields["voice"],
                                "wing": {"id": int(outfit_fields["wing"]), "mask": 0, "pat": 0, "tex": 0}
                            }

                            friend_response["outfit"] = formatted_outfit

                        except Exception as e:
                            logger.error(f"Error building outfit for friend {fs.friend_id}: {e}")
                            friend_response["outfit"] = None
                    else:
                        friend_response["outfit"] = None

                    if hasattr(fs, 'given') and fs.given is not None:
                        friend_response["given"] = int(fs.given)

                    if hasattr(fs, 'player_badge_type') and fs.player_badge_type is not None:
                        friend_response["player_badge_type"] = int(fs.player_badge_type)

                    friend_statues_response.append(friend_response)

                except Exception as e:
                    logger.error(f"Error processing friend {fs.friend_id}: {e}")

            # ========== 8. 处理星座页面数据 ==========

            constellation_response = []

            if constellation_pages:
                pages_dict = {}

                for cp in constellation_pages:
                    page_key = cp.page_index

                    if page_key not in pages_dict:
                        pages_dict[page_key] = {
                            "page_index": page_key,
                            "friends": []
                        }

                    pages_dict[page_key]["friends"].append({
                        "friend_id": str(cp.friend_id),
                        "constellation_node_index": int(cp.constellation_node_index)
                    })

                # 页号 0..最大页号补满(空页给空 friends):
                # 官方这份数据(见 sky_cn_api_reference 的 get_friend_constellation)
                # 里**没有 page_index 字段**, 客户端只能按数组顺序对齐页号 —— 只回
                # 非空页会让后面/中间的页错位。顺便补上官方有、我们以前没回的 name
                # (分页名库表里没列, 只回空串, 见 update_friend_constellation_pages.py)。
                for page_index in range(0, max(pages_dict.keys()) + 1):
                    page = pages_dict.get(page_index) or {"friends": []}
                    # 确保 friends 按 node_index 排序
                    sorted_friends = sorted(page["friends"],
                                          key=lambda x: x["constellation_node_index"])
                    # 官方形状(**只有** name + friends): 0.33.7 的页解析器
                    # (libBootloader0337.so.c sub_181976C)只读 "name" 和那个 7 字符成员
                    # "friends"(值是槽数组), 页号按数组下标对齐 —— 多给的 page_index
                    # 官方没有, 这里去掉, 与抓包参考一字不差。
                    constellation_response.append({
                        "name": "",
                        "friends": sorted_friends
                    })

            # ========== 9. 构建最终响应 ==========

            response_data = {
                "set_friend_statues": friend_statues_response,
                "constellation_friend_pages": constellation_response,
                "result": "success",
                "user_id": user_id,
                "total_friends": len(friend_statues_response),
                "total_pages": len(constellation_response),
                "timestamp": int(time.time() * 1000)
            }

            logger.info(f"Successfully built response for user: {user_id}")

            # 确保 JSON 编码不会失败
            try:
                json_str = json.dumps(response_data, ensure_ascii=False, separators=(',', ':'))
            except Exception as e:
                logger.error(f"JSON encoding error: {e}")
                response_data = {
                    "result": "error",
                    "message": "JSON encoding failed",
                    "code": "JSON_ENCODING_ERROR"
                }
                json_str = json.dumps(response_data, ensure_ascii=False)

            response = Response(
                response=json_str,
                status=200,
                content_type='application/json; charset=utf-8'
            )

            # 添加 CORS 头
            response.headers.add('Access-Control-Allow-Origin', '*')
            response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
            response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')

            response.headers.add('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            response.headers.add('Pragma', 'no-cache')
            response.headers.add('Expires', '0')

            return response

        except Exception as e:
            logger.error(f"Database or processing error: {e}", exc_info=True)
            
            error_response = {
                "result": "error",
                "message": "Database error occurred",
                "code": "DB_ERROR"
            }
            
            # 原来这里写的是 app.config, 但本模块从未 import app -> DB 异常时
            # 会二次抛 NameError, 客户端拿到的是"接口挂掉"而不是 DB 错误。
            if current_app.config.get('DEBUG', False):
                error_response["details"] = str(e)
            
            response = Response(
                response=json.dumps(error_response, ensure_ascii=False),
                status=500,
                content_type='application/json; charset=utf-8'
            )
            response.headers.add('Access-Control-Allow-Origin', '*')
            return response
            
    except Exception as e:
        logger.error(f"Unexpected error in get_friend_statues: {e}", exc_info=True)
        
        error_response = {
            "result": "error",
            "message": "Internal server error",
            "code": "INTERNAL_ERROR"
        }
        
        response = Response(
            response=json.dumps(error_response, ensure_ascii=False),
            status=500,
            content_type='application/json; charset=utf-8'
        )
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response