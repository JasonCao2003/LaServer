# -*- coding: utf-8 -*-
"""
数据库迁移函数
确保所有表结构最新
"""

def migrate_database(db):
    """完整的数据库迁移函数，确保所有表结构最新，包含所有社交动态相关表"""
    from sqlalchemy import inspect, text
    from datetime import datetime
    import json
    import time
    import os
    import uuid
    from sqlalchemy.exc import OperationalError

    inspector = inspect(db.engine)
    print("=" * 60)
    print("开始执行数据库迁移...")
    print("=" * 60)

    # 辅助函数：安全添加列，忽略“列已存在”错误
    def add_column_if_not_exists(table_name, column_name, column_type, default=None):
        columns = [col['name'] for col in inspector.get_columns(table_name)]
        if column_name not in columns:
            try:
                if default is not None:
                    # 处理默认值为字符串的情况（如 "'0.0.0.0'"）
                    if isinstance(default, str) and default.startswith("'") and default.endswith("'"):
                        sql = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type} DEFAULT {default}"
                    else:
                        sql = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type} DEFAULT {default}"
                else:
                    sql = f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
                with db.engine.begin() as conn:
                    conn.execute(text(sql))
                print(f"成功为 {table_name} 表添加列: {column_name}")
            except OperationalError as e:
                if "duplicate column name" in str(e).lower():
                    print(f"列 {column_name} 已存在，跳过添加")
                else:
                    raise e
            except Exception as e:
                print(f"为 {table_name} 表添加列 {column_name} 失败: {e}")

    # ========== 1. user 表字段补充 ==========
    if 'user' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('user')]
        print(f"User 表现有列: {existing_columns}")

        # 需要添加的列及其定义
        columns_to_add = [
            ('is_banned', 'INTEGER', 0),
            ('ban_duration', 'INTEGER', 0),
            ('ban_start_time', 'INTEGER', 0),
            ('ban_end_time', 'INTEGER', 0),
            ('is_authorized', 'INTEGER', 0),
            ('ip_address', 'VARCHAR(50)', "'0.0.0.0'"),
            ('created_at', 'INTEGER', 0),
            ('remark', 'TEXT', "''"),
            ('device_identifier', 'VARCHAR(36)', "''"),
            ('device_name', 'VARCHAR(255)', "''"),
            ('device_type', 'VARCHAR(50)', "''"),
            ('device_model', 'VARCHAR(100)', "''"),
            ('device_os', 'VARCHAR(50)', "''"),
            ('user_agent', 'TEXT', "''")
        ]

        for col_name, col_type, default_val in columns_to_add:
            add_column_if_not_exists('user', col_name, col_type, default_val)

        # 为老用户填充默认设备信息
        print("为现有用户填充默认设备信息...")
        try:
            with db.engine.begin() as conn:
                update_sql = """
                UPDATE user 
                SET device_name = CASE 
                    WHEN device_name IS NULL OR device_name = '' THEN '未知设备'
                    ELSE device_name 
                END,
                device_type = CASE 
                    WHEN device_type IS NULL OR device_type = '' THEN '未知'
                    ELSE device_type 
                END,
                device_model = CASE 
                    WHEN device_model IS NULL OR device_model = '' THEN '未知型号'
                    ELSE device_model 
                END,
                ip_address = CASE 
                    WHEN ip_address IS NULL OR ip_address = '' THEN '0.0.0.0'
                    ELSE ip_address 
                END
                """
                conn.execute(text(update_sql))
                print("设备信息填充完成")
        except Exception as e:
            print(f"填充设备信息失败: {e}")
    else:
        print("User 表不存在，请先初始化数据库")

    # ========== 2. message 表 ==========
    if 'message' not in inspector.get_table_names():
        print("正在创建 message 表...")
        try:
            create_sql = """
            CREATE TABLE message (
                id VARCHAR(255) NOT NULL,
                user_id VARCHAR(36) NOT NULL,
                received_at INT,
                created_at INT DEFAULT 0,
                updated_at INT DEFAULT 0,
                PRIMARY KEY (id, user_id),
                FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
            )
            """
            with db.engine.begin() as conn:
                conn.execute(text(create_sql))
            print("成功创建 message 表")
        except Exception as e:
            print(f"创建 message 表失败: {e}")
    else:
        # 确保所有必要列都存在
        add_column_if_not_exists('message', 'received_at', 'INT')
        add_column_if_not_exists('message', 'created_at', 'INT', 0)
        add_column_if_not_exists('message', 'updated_at', 'INT', 0)

        # 创建索引（此时列已存在）
        with db.engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_message_user_id ON message (user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_message_received_at ON message (received_at)"))
            print("已确保 message 表索引存在")

    # ========== user_currency: 季节爱心列 ==========
    # 游戏货币显示需要 season_heart(季节爱心), 旧库缺该列 -> 货币面板不显示季节爱心。
    add_column_if_not_exists('user_currency', 'season_heart', 'INTEGER', 0)
    # 季卡券 season_pass_token: 内购 SPASSG/SPASSP3 发放, 旧库没有该列。
    add_column_if_not_exists('user_currency', 'season_pass_token', 'INTEGER', 0)

    # ========== 3. user_wing_buff 表 ==========
    if 'user_wing_buff' not in inspector.get_table_names():
        print("正在创建 user_wing_buff 表...")
        try:
            create_sql = """
            CREATE TABLE user_wing_buff (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id VARCHAR(36) NOT NULL,
                name VARCHAR(255) NOT NULL,
                collected BOOLEAN DEFAULT FALSE,
                deposited BOOLEAN DEFAULT FALSE,
                last_conversion BIGINT DEFAULT 0,
                deposit_id BIGINT DEFAULT 0,
                created_at INT DEFAULT 0,
                updated_at INT DEFAULT 0,
                UNIQUE (user_id, name)
            )
            """
            with db.engine.begin() as conn:
                conn.execute(text(create_sql))
            print("成功创建 user_wing_buff 表")
        except Exception as e:
            print(f"创建 user_wing_buff 表失败: {e}")
    else:
        add_column_if_not_exists('user_wing_buff', 'created_at', 'INT', 0)
        add_column_if_not_exists('user_wing_buff', 'updated_at', 'INT', 0)

        with db.engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_user_wing_buff_user_id ON user_wing_buff (user_id)"))
            print("已确保 user_wing_buff.user_id 索引存在")

    # ========== 4. user_initialization 表 ==========
    if 'user_initialization' not in inspector.get_table_names():
        print("正在创建 user_initialization 表...")
        try:
            create_sql = """
            CREATE TABLE user_initialization (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id VARCHAR(36) NOT NULL UNIQUE,
                is_initialized BOOLEAN DEFAULT FALSE,
                created_at INT DEFAULT 0,
                updated_at INT DEFAULT 0
            )
            """
            with db.engine.begin() as conn:
                conn.execute(text(create_sql))
            print("成功创建 user_initialization 表")
        except Exception as e:
            print(f"创建 user_initialization 表失败: {e}")
    else:
        add_column_if_not_exists('user_initialization', 'created_at', 'INT', 0)
        add_column_if_not_exists('user_initialization', 'updated_at', 'INT', 0)

        with db.engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_user_initialization_user_id ON user_initialization (user_id)"))
            print("已确保 user_initialization.user_id 索引存在")

    # ========== 5. invite 表 ==========
    if 'invite' not in inspector.get_table_names():
        print("正在创建 invite 表...")
        try:
            create_sql = """
            CREATE TABLE invite (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                inviter_user_id VARCHAR(36) NOT NULL,
                token_id VARCHAR(36) NOT NULL UNIQUE,
                formatted_code VARCHAR(14),
                nickname VARCHAR(100) NOT NULL,
                friend_id VARCHAR(36),
                created INTEGER NOT NULL,
                updated_at DATETIME
            )
            """
            with db.engine.begin() as conn:
                conn.execute(text(create_sql))
            print("成功创建 invite 表")
        except Exception as e:
            print(f"创建 invite 表失败: {e}")
    else:
        add_column_if_not_exists('invite', 'formatted_code', 'VARCHAR(14)')
        add_column_if_not_exists('invite', 'updated_at', 'DATETIME')

        # 为 updated_at 设置默认值（如果该列刚添加且已有数据）
        try:
            with db.engine.begin() as conn:
                # 检查是否有 updated_at 为 NULL 的记录
                result = conn.execute(text("SELECT COUNT(*) FROM invite WHERE updated_at IS NULL")).scalar()
                if result > 0:
                    current_time = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
                    conn.execute(text(f"UPDATE invite SET updated_at = '{current_time}' WHERE updated_at IS NULL"))
                    print("成功设置 updated_at 默认值")
        except Exception as e:
            print(f"设置 updated_at 默认值失败: {e}")

        # 索引和唯一约束
        with db.engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_inviter_user_id ON invite (inviter_user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_friend_id ON invite (friend_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_token_id ON invite (token_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_formatted_code ON invite (formatted_code)"))
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS unique_formatted_code ON invite (formatted_code)"))
            print("已确保 invite 表索引和唯一约束")

    # ========== 6. friend_status 表 ==========
    if 'friend_status' not in inspector.get_table_names():
        print("正在创建 friend_status 表...")
        try:
            create_sql = """
            CREATE TABLE friend_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id VARCHAR(36) NOT NULL,
                friend_id VARCHAR(36) NOT NULL,
                given INTEGER DEFAULT 0,
                recvd INTEGER DEFAULT 0,
                nickname VARCHAR(100) NOT NULL,
                when_created INTEGER NOT NULL,
                level INTEGER DEFAULT 1,
                abilities TEXT,
                hints TEXT,
                outfit TEXT,
                player_badge_type INTEGER DEFAULT 0,
                local_soft_deleted BOOLEAN DEFAULT 0,
                attitude VARCHAR(10) DEFAULT '0',
                created_at DATETIME,
                updated_at DATETIME,
                UNIQUE (user_id, friend_id)
            )
            """
            with db.engine.begin() as conn:
                conn.execute(text(create_sql))
            print("成功创建 friend_status 表")
        except Exception as e:
            print(f"创建 friend_status 表失败: {e}")
    else:
        # 检查是否有缺失列（可根据实际模型补充，此处仅示例）
        # 如果有需要添加的列，可在此处调用 add_column_if_not_exists
        print("friend_status 表已存在")

    # ========== 7. friendship 表 ==========
    if 'friendship' not in inspector.get_table_names():
        print("正在创建 friendship 表...")
        try:
            create_sql = """
            CREATE TABLE friendship (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id VARCHAR(36) NOT NULL,
                friend_id VARCHAR(36) NOT NULL,
                status VARCHAR(20) DEFAULT 'active',
                created_at DATETIME,
                updated_at DATETIME,
                UNIQUE (user_id, friend_id)
            )
            """
            with db.engine.begin() as conn:
                conn.execute(text(create_sql))
            print("成功创建 friendship 表")
        except Exception as e:
            print(f"创建 friendship 表失败: {e}")
    else:
        print("friendship 表已存在")

    # ========== 8. user_online_status 表 ==========
    if 'user_online_status' not in inspector.get_table_names():
        print("正在创建 user_online_status 表...")
        try:
            create_sql = """
            CREATE TABLE user_online_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id VARCHAR(36) NOT NULL,
                friend_id VARCHAR(36) NOT NULL,
                is_online BOOLEAN DEFAULT 0,
                last_seen DATETIME,
                created_at DATETIME,
                updated_at DATETIME
            )
            """
            with db.engine.begin() as conn:
                conn.execute(text(create_sql))
            print("成功创建 user_online_status 表")
        except Exception as e:
            print(f"创建 user_online_status 表失败: {e}")
    else:
        print("user_online_status 表已存在")

    # ========== 9. constellation_friend_page 表 ==========
    if 'constellation_friend_page' not in inspector.get_table_names():
        print("正在创建 constellation_friend_page 表...")
        try:
            create_sql = """
            CREATE TABLE constellation_friend_page (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id VARCHAR(36) NOT NULL,
                page_index INTEGER NOT NULL DEFAULT 0,
                friend_id VARCHAR(36) NOT NULL,
                constellation_node_index INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER DEFAULT 0,
                updated_at INTEGER DEFAULT 0,
                UNIQUE (user_id, page_index, friend_id),
                FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE,
                FOREIGN KEY (friend_id) REFERENCES user(id) ON DELETE CASCADE
            )
            """
            with db.engine.begin() as conn:
                conn.execute(text(create_sql))
            print("成功创建 constellation_friend_page 表")

            # 初始化数据（从 friend_status 迁移）
            print("正在初始化星座好友页面数据...")
            try:
                with db.engine.begin() as conn:
                    users_with_friends = conn.execute(text("""
                        SELECT DISTINCT user_id FROM friend_status
                    """)).fetchall()
                    for (user_id,) in users_with_friends:
                        friends = conn.execute(
                            text("SELECT friend_id, when_created FROM friend_status WHERE user_id = :uid ORDER BY when_created DESC"),
                            {"uid": user_id}
                        ).fetchall()
                        for idx, (friend_id, _) in enumerate(friends):
                            page = idx // 50
                            node = idx % 50
                            conn.execute(
                                text("""
                                    INSERT OR IGNORE INTO constellation_friend_page 
                                    (user_id, page_index, friend_id, constellation_node_index, created_at, updated_at)
                                    VALUES (:uid, :page, :fid, :node, :now, :now)
                                """),
                                {"uid": user_id, "page": page, "fid": friend_id, "node": node, "now": int(time.time())}
                            )
                    print("星座好友页面数据初始化完成")
            except Exception as e:
                print(f"初始化星座页面数据失败: {e}")
        except Exception as e:
            print(f"创建 constellation_friend_page 表失败: {e}")
    else:
        add_column_if_not_exists('constellation_friend_page', 'created_at', 'INTEGER', 0)
        add_column_if_not_exists('constellation_friend_page', 'updated_at', 'INTEGER', 0)

        with db.engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_constellation_user_id ON constellation_friend_page (user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_constellation_friend_id ON constellation_friend_page (friend_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_constellation_page_index ON constellation_friend_page (page_index)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_constellation_user_page ON constellation_friend_page (user_id, page_index)"))
            print("已确保 constellation_friend_page 表索引存在")

    # ========== 10. social_feed_post 表（社交动态）==========
    if 'social_feed_post' not in inspector.get_table_names():
        print("正在创建 social_feed_post 表...")
        try:
            create_sql = """
            CREATE TABLE social_feed_post (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id VARCHAR(36) NOT NULL,
                social_feed_id VARCHAR(36) NOT NULL UNIQUE,
                pool_type VARCHAR(50) NOT NULL,
                pool_name VARCHAR(100) NOT NULL,
                message TEXT NOT NULL,
                created_at INT DEFAULT 0,
                expire_at INT DEFAULT 0,
                likes_count INT DEFAULT 0,
                comments_count INT DEFAULT 0,
                report_count INT DEFAULT 0,
                hide_count INT DEFAULT 0,
                attempt_count INT DEFAULT 0,
                succeed_count INT DEFAULT 0,
                is_poisoned BOOLEAN DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE
            )
            """
            with db.engine.begin() as conn:
                conn.execute(text(create_sql))
            print("成功创建 social_feed_post 表")

            # 创建索引
            with db.engine.begin() as conn:
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_social_feed_user_id ON social_feed_post (user_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_social_feed_pool ON social_feed_post (pool_type, pool_name)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_social_feed_expire ON social_feed_post (expire_at)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_social_feed_pool_expire ON social_feed_post (pool_type, pool_name, expire_at, created_at DESC)"))
            print("已为 social_feed_post 表创建索引")
        except Exception as e:
            print(f"创建 social_feed_post 表失败: {e}")
    else:
        # 确保所有必要列存在
        feed_columns = [
            ('expire_at', 'INT', 0),
            ('likes_count', 'INT', 0),
            ('comments_count', 'INT', 0),
            ('report_count', 'INT', 0),
            ('hide_count', 'INT', 0),
            ('attempt_count', 'INT', 0),
            ('succeed_count', 'INT', 0),
            ('is_poisoned', 'BOOLEAN', 0)
        ]
        for col_name, col_type, default_val in feed_columns:
            add_column_if_not_exists('social_feed_post', col_name, col_type, default_val)

        # 确保索引存在
        with db.engine.begin() as conn:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_social_feed_user_id ON social_feed_post (user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_social_feed_pool ON social_feed_post (pool_type, pool_name)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_social_feed_expire ON social_feed_post (expire_at)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_social_feed_pool_expire ON social_feed_post (pool_type, pool_name, expire_at, created_at DESC)"))
            print("已确保 social_feed_post 表索引完整")

    # ========== 11. social_feed_like 表（点赞记录）==========
    if 'social_feed_like' not in inspector.get_table_names():
        print("正在创建 social_feed_like 表...")
        try:
            create_sql = """
            CREATE TABLE social_feed_like (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id VARCHAR(36) NOT NULL,
                social_feed_id VARCHAR(36) NOT NULL,
                created_at INT DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES user(id) ON DELETE CASCADE,
                FOREIGN KEY (social_feed_id) REFERENCES social_feed_post(social_feed_id) ON DELETE CASCADE,
                UNIQUE (user_id, social_feed_id)
            )
            """
            with db.engine.begin() as conn:
                conn.execute(text(create_sql))
            print("成功创建 social_feed_like 表")

            # 索引
            with db.engine.begin() as conn:
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_social_feed_like_user ON social_feed_like (user_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_social_feed_like_feed ON social_feed_like (social_feed_id)"))
            print("已为 social_feed_like 表创建索引")
        except Exception as e:
            print(f"创建 social_feed_like 表失败: {e}")
    else:
        # 确保 created_at 列存在
        add_column_if_not_exists('social_feed_like', 'created_at', 'INT', 0)
        print("social_feed_like 表已存在")

    # ========== 11.5 修复 collectible 主键 (id 全表唯一 -> 自增主键) ==========
    # 旧表 PRIMARY KEY(id) 导致"cape"等收藏品全表唯一, 多个用户无法各自拥有同名收藏品
    # (collect_collectible 报 UNIQUE constraint failed: collectible.id)。
    # 重建为: row_id 自增主键 + UNIQUE(user_id, id)。
    try:
        if 'collectible' in inspector.get_table_names():
            cols = [c['name'] for c in inspector.get_columns('collectible')]
            if 'row_id' not in cols:
                print("正在迁移 collectible 表主键...")
                with db.engine.begin() as conn:
                    conn.execute(text("ALTER TABLE collectible RENAME TO collectible_old"))
                    conn.execute(text("""
                        CREATE TABLE collectible (
                            row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                            id VARCHAR(100) NOT NULL,
                            user_id VARCHAR(36) NOT NULL,
                            used BOOLEAN,
                            candle_space BOOLEAN,
                            carrying BOOLEAN,
                            level INTEGER,
                            permanent_level INTEGER,
                            created_at DATETIME,
                            updated_at DATETIME,
                            CONSTRAINT unique_user_collectible UNIQUE (user_id, id)
                        )
                    """))
                    conn.execute(text("""
                        INSERT INTO collectible
                            (id, user_id, used, candle_space, carrying,
                             level, permanent_level, created_at, updated_at)
                        SELECT id, user_id, used, candle_space, carrying,
                               level, permanent_level, created_at, updated_at
                        FROM collectible_old
                    """))
                    conn.execute(text("DROP TABLE collectible_old"))
                    conn.execute(text(
                        "CREATE INDEX IF NOT EXISTS idx_collectible_user "
                        "ON collectible (user_id)"))
                print("collectible 表主键迁移完成")
            else:
                print("collectible 表已是新结构")
    except Exception as e:
        print(f"collectible 表迁移失败: {e}")

    # ========== 12. 数据库性能优化 ==========
    try:
        print("正在执行数据库优化...")
        with db.engine.begin() as conn:
            # 清理未使用的空间
            conn.execute(text("VACUUM"))
            # 更新统计信息
            conn.execute(text("ANALYZE"))
        print("数据库优化完成")
    except Exception as e:
        print(f"数据库优化失败: {e}")

    print("=" * 60)
    print("数据库迁移全部完成！")
    print("=" * 60)