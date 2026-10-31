from flask import request, jsonify, Response, current_app
from database.models import db, User, Friendship, FriendStatus, UserOnlineStatus, UserOutfit, ConstellationFriendPage
import helpers.friend_helper as friend_helper
import json
import time
import logging

logger = logging.getLogger(__name__)

def get_cached_user(user_id, session_token):
    """获取缓存的用户会话（简化版）"""
    user = User.query.filter_by(id=user_id, session=session_token).first()
    return user

def get_friends():
    """获取用户的好友列表 - 修复版本"""
    try:
        # 1. 获取并解析请求数据
        raw_data = request.get_data(as_text=True)
        logger.info(f"get_friends 原始请求数据: {raw_data}")
        
        if not raw_data or raw_data.strip() == '':
            logger.error("get_friends: 请求体为空")
            return Response(
                response=json.dumps({
                    "result": "error",
                    "message": "Invalid request body",
                    "code": "EMPTY_BODY"
                }),
                status=400,
                mimetype='application/json'
            )
        
        # 尝试解析JSON
        try:
            request_data = json.loads(raw_data)
            logger.info(f"get_friends 解析后的请求数据: {request_data}")
        except json.JSONDecodeError as e:
            logger.error(f"get_friends: JSON解析错误: {e}")
            return Response(
                response=json.dumps({
                    "result": "error", 
                    "message": "Invalid JSON format",
                    "code": "INVALID_JSON"
                }),
                status=400,
                mimetype='application/json'
            )
        
        # 2. 验证必需字段
        user_id = request_data.get('user')
        session_token = request_data.get('session')
        
        if not user_id:
            logger.error("get_friends: 缺少 user 字段")
            return Response(
                response=json.dumps({
                    "result": "error",
                    "message": "Missing 'user' field",
                    "code": "MISSING_USER"
                }),
                status=400,
                mimetype='application/json'
            )
        
        if not session_token:
            logger.error("get_friends: 缺少 session 字段")
            return Response(
                response=json.dumps({
                    "result": "error",
                    "message": "Missing 'session' field",
                    "code": "MISSING_SESSION"
                }),
                status=400,
                mimetype='application/json'
            )
        
        # 3. 验证用户会话
        user = get_cached_user(user_id, session_token)
        if not user:
            logger.warning(f"get_friends: 用户会话无效 - user_id: {user_id}")
            return Response(
                response=json.dumps({
                    "result": "error",
                    "message": "Invalid session or user not found",
                    "code": "INVALID_SESSION"
                }),
                status=401,
                mimetype='application/json'
            )
        
        # 4. 获取可选的分页参数
        page_max = request_data.get('page_max', 50)  # 默认每页50个
        page_offset = request_data.get('page_offset', 0)
        players = request_data.get('players', [])  # 特定玩家列表（可选）
        
        logger.info(f"get_friends: 查询用户 {user_id} 的好友列表, page_max={page_max}, page_offset={page_offset}")
        
        # 5. 查询好友关系
        try:
            # 客户端"点亮"某个玩家后, 会用 players=[<uuid>] 在这里每 200~300ms 追问一次。
            # 这个动作不经过任何会写 friendship 表的接口 -> 库里拿不到关系 -> 好友树空白
            # 且客户端无限重试(实测 690 次)。所以先把点名/同关卡在线的人补成好友关系。
            requested = []
            for item in (players if isinstance(players, list) else []):
                if isinstance(item, str) and item.strip() and item.strip() != user_id:
                    requested.append(item.strip())
            if requested:
                logger.info(f"get_friends: 查询特定玩家列表: {requested}")

            try:
                # allow_placeholder=True: 客户端点名的 uuid 如果从没登录过本服,
                # 补一条占位账号, 让点亮关系真正落库(否则换地图就得重新点火)。
                friend_helper.sync_lit_relationships(user_id, requested, allow_placeholder=True)
            except Exception as e:
                logger.warning(f"get_friends: 同步点亮关系失败: {e}")
                db.session.rollback()

            query = Friendship.query.filter_by(
                user_id=user_id,
                status='active'
            )

            if requested:
                query = query.filter(Friendship.friend_id.in_(requested))

            friendships = query.order_by(Friendship.updated_at.desc())\
                              .offset(page_offset)\
                              .limit(page_max)\
                              .all()

            logger.info(f"get_friends: 找到 {len(friendships)} 个活跃好友")

            # 在线判定改用 UDP 节点(真正连着 ENet 的人), 而不是从来没被写过
            # 的 user_online_status 表(那张表恒为空 -> 所有人都显示离线)。
            try:
                online_ids = {p.get('uuid') for p in friend_helper.node_players() if p.get('uuid')}
            except Exception:
                online_ids = set()

            # 拉黑标志(客户端 +691 blocked / +692 local_blocked, 见 friend_helper.block_flags)。
            # 批量查一次, 别在 250ms 一次的轮询里给每个好友各打两次查询:
            #   blocked_ids       = "我拉黑的人"(我这一行 status='blocked'; 上面的主查询
            #                       只取 'active', 所以这批人本来就不在 set_friends 里)
            #   local_blocked_ids = "拉黑了我的人"(反方向那一行 status='blocked') —— 这一位
            #                       以前从来不下发, 被拉黑的一方看起来一切正常。
            try:
                blocked_ids = {row.friend_id for row in Friendship.query.filter_by(
                    user_id=user_id, status='blocked').all()}
            except Exception as e:
                logger.warning(f"get_friends: 查询 blocked_ids 失败: {e}")
                blocked_ids = set()
            try:
                local_blocked_ids = {row.user_id for row in Friendship.query.filter_by(
                    friend_id=user_id, status='blocked').all()}
            except Exception as e:
                logger.warning(f"get_friends: 查询 local_blocked_ids 失败: {e}")
                local_blocked_ids = set()

            # 主控删掉的好友(墓碑行 status='removed', 见 friend_helper.set_friendship_state)。
            # 这批人既不该出现在 set_friends 里, 也不能被下面的"点名兜底"合成成正常好友 ——
            # 但点名时又必须照回一份资料(不回客户端会无限重试), 所以回的时候带 soft_deleted
            # 标志(0.33.7 条目解析 +1237/+1238), 让客户端把这段关系当"已删除"处理。
            try:
                removed_ids = {row.friend_id for row in Friendship.query.filter_by(
                    user_id=user_id, status='removed').all()}
                removed_ids |= {row.user_id for row in Friendship.query.filter_by(
                    friend_id=user_id, status='removed').all()}
            except Exception as e:
                logger.warning(f"get_friends: 查询 removed_ids 失败: {e}")
                removed_ids = set()

            # 6. 构建好友列表
            #
            # 必须是 **JSON 数组**(list), 不能是以 friend_id 为键的对象(dict)。
            # 客户端反编译 sub_408898(set_friends/update_friends 的解析器)是这样遍历的:
            #
            #     v24 = container.data;                 // 每个槽 16 字节 = {key, value}
            #     if ( *(DWORD*)container.data )        // slot[0].key != NULL -> 这是"对象"
            #         goto LABEL_13;                    //   -> 跳过, 一个都不解析
            # LABEL_15:  sub_7C0890(&uuid, slot.value); // 解析一个好友条目
            # LABEL_13:  while (++i < count)
            #                if ( !*(QWORD*)(data + 16*i) )  // 只有 key==NULL 的槽
            #                    goto LABEL_15;             // 才会被当成数组元素解析
            #
            # 也就是说: 每个槽的 key 必须为空指针 —— 只有真正的 JSON 数组才会这样;
            # 一旦回成 {"<friend_uuid>": {...}} 这种对象, 所有槽都有 key, 客户端会把
            # 整份列表全部跳过(set_friends 等于空)。这正好解释了:
            #   - 同房间的两位好友显示成陌生人(客户端好友表里根本没有对方);
            #   - 好友树不同步(条目里的 abilities/hints 从来读不到, 不会合并到本地);
            #   - 只有 give_candle 才能"恢复"(它回的 update_friends 本来就是数组)。
            set_friends = []
            seen_friends = set()

            for friendship in friendships:
                try:
                    friend_id = friendship.friend_id
                    friend_status = FriendStatus.query.filter_by(
                        user_id=user_id,
                        friend_id=friend_id
                    ).first()
                    outfit = UserOutfit.query.filter_by(user_id=friend_id).first()

                    friend_info = friend_helper.build_friend_entry(
                        user_id, friend_id, friendship, friend_status, outfit, online_ids,
                        blocked=(friend_id in blocked_ids),
                        local_blocked=(friend_id in local_blocked_ids),
                        soft_deleted=(friend_id in removed_ids))
                    if friend_info is None:
                        logger.warning(f"get_friends: 好友 {friend_id} 不存在")
                        continue

                    # 只回"真的加过好友"的条目(见 friend_helper.is_added_friend):
                    # "点亮"出来的行(abilities=[] 且没换过蜡烛)是陌生关系 —— 以前照样
                    # 当好友下发, 客户端把刚点亮的陌生人画成好友还给出"设置备注"按钮,
                    # 用户实测 2026-10-05: "两个新建的号为什么会默认为好友 这是错误的" /
                    # "为什么陌生人状态下会出现设置备注的按钮"。拉黑的照旧要回(拉黑标志
                    # 得显示出来), 主控删掉的墓碑行也照旧要回(见下面 soft_deleted 的说明)。
                    if not (friend_helper.is_added_friend(friend_status)
                            or friend_id in blocked_ids
                            or friend_id in local_blocked_ids
                            or friend_id in removed_ids):
                        logger.info(f"get_friends: {friend_id} 只是点亮/陌生人, 不下发")
                        continue

                    if friend_id in seen_friends:
                        continue
                    seen_friends.add(friend_id)
                    set_friends.append(friend_info)

                except Exception as e:
                    logger.error(f"get_friends: 处理好友 {friendship.friend_id} 时出错: {e}")
                    continue

            # 6.1 兜底: 客户端点名要的 uuid, 即使上面因为并发/写库失败没查到关系,
            #     也必须回一份结构完整的资料 —— 否则客户端拿不到就会一直重试。
            #
            # 例外: **库里有关系、但那段关系只是"点亮"**(is_added_friend 为假)时不回。
            # 这一步以前对任何点名的 uuid 都合成一份完整好友资料(名字还是对方的设备名),
            # 客户端于是把同关卡刚点亮的陌生人画成好友 + 给"设置备注"按钮 ——
            # 正是用户 2026-10-05 报的两个现象。官方对"没加过的玩家"回复里根本不含这条。
            # 拉黑/主控删除的例外照旧要回(客户端在等这两类状态)。
            for friend_id in requested:
                if friend_id in seen_friends:
                    continue
                try:
                    if friend_id not in blocked_ids and friend_id not in local_blocked_ids \
                            and friend_id not in removed_ids:
                        existing_status = FriendStatus.query.filter_by(
                            user_id=user_id, friend_id=friend_id).first()
                        if not friend_helper.is_added_friend(existing_status):
                            logger.info(
                                f"get_friends: 点名的 {friend_id} 不是好友(只是点亮/陌生), 不合成条目")
                            continue
                    outfit = UserOutfit.query.filter_by(user_id=friend_id).first()
                    friend_info = friend_helper.build_friend_entry(
                        user_id, friend_id, None, None, outfit, online_ids,
                        blocked=(friend_id in blocked_ids),
                        local_blocked=(friend_id in local_blocked_ids),
                        soft_deleted=(friend_id in removed_ids))
                    if friend_info is None:
                        continue
                    logger.info(f"get_friends: 为点名的玩家 {friend_id} 合成好友资料(库中暂无关系)")
                    seen_friends.add(friend_id)
                    set_friends.append(friend_info)
                except Exception as e:
                    logger.error(f"get_friends: 合成 {friend_id} 资料失败: {e}")
                    continue

            # 7. 构建响应
            #
            # set_friend_count: sub_40858C 里对它的值是 sscanf("%u") 解析的标量
            # (官方 account__give_candle.json 里也是数字 96), 顺手补上。
            response_data = {
                "set_friends": set_friends,
                "set_friend_count": len(set_friends),
                "result": "success",
                "user_id": user_id,
                "total_friends": len(set_friends),
                "page_max": page_max,
                "page_offset": page_offset,
                "timestamp": int(time.time() * 1000)
            }
            
            logger.info(f"get_friends: 成功返回 {len(set_friends)} 个好友信息")
            
            # 8. 返回响应
            return Response(
                response=json.dumps(response_data, ensure_ascii=False, separators=(',', ':')),
                status=200,
                mimetype='application/json; charset=utf-8',
                headers={
                    'Access-Control-Allow-Origin': '*',
                    'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
                    'Pragma': 'no-cache'
                }
            )
            
        except Exception as e:
            logger.error(f"get_friends: 数据库查询错误: {e}", exc_info=True)
            return Response(
                response=json.dumps({
                    "result": "error",
                    "message": "Database error occurred",
                    "code": "DB_ERROR",
                    # 原来这里写的是 app.config, 但本模块从未 import app ->
                    # 一旦真出 DB 异常就会二次抛 NameError, 500 里连 details 都拿不到。
                    "details": str(e) if current_app.config.get('DEBUG', False) else ""
                }),
                status=500,
                mimetype='application/json'
            )
            
    except Exception as e:
        logger.error(f"get_friends: 未预期的错误: {e}", exc_info=True)
        return Response(
            response=json.dumps({
                "result": "error",
                "message": "Internal server error",
                "code": "INTERNAL_ERROR"
            }),
            status=500,
            mimetype='application/json'
        )