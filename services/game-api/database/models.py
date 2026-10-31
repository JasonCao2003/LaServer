# -*- coding: utf-8 -*-
"""
数据库模型定义
包含所有account接口需要的数据库模型
"""
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone
import uuid
import time

db = SQLAlchemy()


class FlexibleDateTime(db.TypeDecorator):
    """兼容"库里存的是 int 秒 / 各种字符串"的 DateTime 列。

    背景(2026-10-01 实测): 管理层"推送全解锁/全动作"是用**裸 SQL** 写的 collectible,
    created_at/updated_at 直接塞了 int(time.time()); 而模型里这两列声明的是 DateTime,
    SQLAlchemy 读库时会调 str_to_datetime ->
        TypeError: fromisoformat: argument must be str
    /account/get_collectibles 整个接口 500(客户端连重试 8 次后放弃), 玩家看到的就是
    "登录到一半连接失败"。

    这里读写两个方向都做归一化:
      · 出库: int/float(秒) -> naive UTC datetime, 文本按常见几种格式解析;
      · 入库: datetime 原样交给 DateTime 处理, int/str 先转成 datetime;
      · 认不出来的值一律当 None —— **绝不让一行脏数据把整个接口搞崩**。
    """
    impl = db.DateTime
    cache_ok = True

    _FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S")

    @classmethod
    def _to_dt(cls, value):
        if value is None or isinstance(value, datetime):
            return value
        if isinstance(value, bool):          # bool 是 int 的子类, 别当成时间戳
            return None
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value),
                                              timezone.utc).replace(tzinfo=None)
            except (OverflowError, OSError, ValueError):
                return None
        s = str(value).strip()
        for fmt in cls._FORMATS:
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                pass
        return None

    def process_bind_param(self, value, dialect):
        return self._to_dt(value)

    def process_result_value(self, value, dialect):
        return self._to_dt(value)

    def result_processor(self, dialect, coltype):
        """直接接管取值处理, **不走 impl(DateTime)的处理器**。

        为什么必须绕开: TypeDecorator 默认是 "impl 的处理器先跑, 再跑
        process_result_value"(见 SQLAlchemy type_api.py result_processor),
        而 DateTime 在 SQLite 上的处理器是 str_to_datetime —— 它拿到 int 就已经
        `TypeError: fromisoformat: argument must be str` 抛出来了, 根本轮不到
        process_result_value 兜底。
        """
        return self._to_dt


class User(db.Model):
    __tablename__ = 'user'
    
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    device = db.Column(db.String(255), nullable=True, default='')
    device_identifier = db.Column(db.String(36), default=lambda: str(uuid.uuid4()))
    session = db.Column(db.String(32), unique=True, default=lambda: uuid.uuid4().hex)
    recovery_token = db.Column(db.String(64), default=lambda: uuid.uuid4().hex)
    version = db.Column(db.Integer)
    platform = db.Column(db.String(255), nullable=True, default='')
    
    is_banned = db.Column(db.Integer, default=0)
    ban_duration = db.Column(db.Integer, default=0)
    ban_start_time = db.Column(db.Integer, default=0)
    ban_end_time = db.Column(db.Integer, default=0)
    
    is_authorized = db.Column(db.Integer, default=0)
    ip_address = db.Column(db.String(50), default='0.0.0.0')
    created_at = db.Column(db.Integer, default=lambda: int(time.time()))
    remark = db.Column(db.Text, nullable=True, default='')
    
    device_name = db.Column(db.String(255), nullable=True, default='')
    device_type = db.Column(db.String(50), nullable=True, default='')
    device_model = db.Column(db.String(100), nullable=True, default='')
    device_os = db.Column(db.String(50), nullable=True, default='')
    user_agent = db.Column(db.Text, nullable=True, default='')
    
    def __repr__(self):
        return f'<User {self.id}>'
    
    def to_dict(self):
        """将用户对象转换为字典"""
        return {
            'id': self.id,
            'device': self.device,
            'device_identifier': self.device_identifier,
            'session': self.session,
            'version': self.version,
            'platform': self.platform,
            'is_banned': self.is_banned,
            'is_authorized': self.is_authorized,
            'ip_address': self.ip_address,
            'created_at': self.created_at,
            'device_name': self.device_name,
            'device_type': self.device_type,
            'device_model': self.device_model,
            'device_os': self.device_os
        }

class Version(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    version = db.Column(db.Integer)

def zifuchuan(length):
    uid = uuid.uuid4()
    uid_str = str(uid).replace('-', '')
    return uid_str[:length]

class UserOnlineStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    friend_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    is_online = db.Column(db.Boolean, default=False)
    last_seen = db.Column(db.DateTime, default=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('online_statuses', lazy=True))
    friend = db.relationship('User', foreign_keys=[friend_id])

class Invite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    inviter_user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    token_id = db.Column(db.String(36), nullable=False, default=lambda: str(uuid.uuid4()))
    formatted_code = db.Column(db.String(14), nullable=True)  # 格式化邀请码
    nickname = db.Column(db.String(100), nullable=False)
    friend_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=True)  # 注意：可为空
    created = db.Column(db.Integer, nullable=False, default=lambda: int(time.time()))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    inviter = db.relationship('User', foreign_keys=[inviter_user_id], backref=db.backref('sent_invites', lazy=True))
    friend = db.relationship('User', foreign_keys=[friend_id], backref=db.backref('received_invites', lazy=True))
    
    # token_id唯一约束
    __table_args__ = (db.UniqueConstraint('token_id', name='unique_token_id'),)

class StatusUnlock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    unlock_type = db.Column(db.String(50), nullable=False, default='level')
    ack = db.Column(db.Boolean, default=False)
    unlocked_at = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', backref=db.backref('status_unlocks', lazy=True))
    __table_args__ = (db.UniqueConstraint('user_id', 'name', name='unique_user_unlock'),)

class DefaultUnlockConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    unlock_type = db.Column(db.String(50), nullable=False, default='level')
    default_ack = db.Column(db.Boolean, default=False)
    default_unlocked_at = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class AchievementDef(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    achievement_id = db.Column(db.String(100), unique=True, nullable=False)  # 
    name = db.Column(db.String(255), nullable=False)  # 
    description = db.Column(db.Text)  # 
    icon_url = db.Column(db.String(500))  # URL
    category = db.Column(db.String(100))  # 
    points = db.Column(db.Integer, default=0)  # 
    is_secret = db.Column(db.Boolean, default=False)  # 
    is_active = db.Column(db.Boolean, default=True)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class FriendStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    friend_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    given = db.Column(db.Integer, default=0)  # 
    recvd = db.Column(db.Integer, default=0)  # 
    nickname = db.Column(db.String(100), nullable=False)
    when_created = db.Column(db.Integer, nullable=False)  # 
    level = db.Column(db.Integer, default=1)
    abilities = db.Column(db.Text)  # JSON
    hints = db.Column(db.Text)  # JSON
    outfit = db.Column(db.Text)  # JSON
    player_badge_type = db.Column(db.Integer, default=0)
    local_soft_deleted = db.Column(db.Boolean, default=False)
    attitude = db.Column(db.String(10), default="0")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # User
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('friend_statuses', lazy=True))
    friend = db.relationship('User', foreign_keys=[friend_id])
    
    __table_args__ = (db.UniqueConstraint('user_id', 'friend_id', name='unique_friend_status'),)

# ========== ConstellationFriendPage 模型增强 ==========

class ConstellationFriendPage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    page_index = db.Column(db.Integer, nullable=False)
    friend_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    constellation_node_index = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.Integer, default=lambda: int(time.time()))
    updated_at = db.Column(db.Integer, default=lambda: int(time.time()), onupdate=lambda: int(time.time()))
    
    # User 关系
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('constellation_pages', lazy=True))
    friend = db.relationship('User', foreign_keys=[friend_id])
    
    __table_args__ = (
        db.UniqueConstraint('user_id', 'page_index', 'friend_id', name='unique_constellation_friend'),
    )
    
    def __repr__(self):
        return f'<ConstellationFriendPage user:{self.user_id} page:{self.page_index} friend:{self.friend_id}>'

class Friendship(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    friend_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='active')  # active, blocked, pending
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # User
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('friendships', lazy=True))
    friend = db.relationship('User', foreign_keys=[friend_id])
    
    __table_args__ = (db.UniqueConstraint('user_id', 'friend_id', name='unique_friendship'),)

class AchievementStats(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    stat_type = db.Column(db.String(100), nullable=False)  # 
    value = db.Column(db.Float, nullable=False)  # 
    update_time = db.Column(db.Integer, nullable=False)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('achievement_stats_track', lazy=True))
    
    __table_args__ = (db.UniqueConstraint('user_id', 'stat_type', name='unique_user_stat_type_track'),)

class EventCurrencyDef(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    currency_id = db.Column(db.String(50), nullable=False, unique=True)  # ID "days_of_aurora"
    event_name = db.Column(db.String(100), nullable=False)  #  "global_aurora_ec_off"
    name_string_key = db.Column(db.String(100), nullable=False)  # 
    hud_icon = db.Column(db.String(100), nullable=False)  # HUD
    client_currency = db.Column(db.String(100), nullable=False)  # 
    forge_cost = db.Column(db.Integer, nullable=True)  # 
    forge_rate_reset_event = db.Column(db.String(100), nullable=False, default="")  # 
    purchasable = db.Column(db.Boolean, nullable=False, default=False)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Infraction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    infraction_type = db.Column(db.String(50), nullable=False)  # 
    reason = db.Column(db.Text)  # 
    severity = db.Column(db.String(20), default='low')  # : low, medium, high
    status = db.Column(db.String(20), default='active')  # : active, resolved, expired
    start_time = db.Column(db.DateTime, default=datetime.utcnow)  # 
    end_time = db.Column(db.DateTime)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('infractions', lazy=True))

class AchievementStat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    stat_type = db.Column(db.String(100), nullable=False)  #  "world_quest_sunset_juggle_count"
    value = db.Column(db.BigInteger, nullable=False)  # 
    updated_at = db.Column(db.Integer, nullable=False)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('achievement_statistics', lazy=True))
    
    __table_args__ = (db.UniqueConstraint('user_id', 'stat_type', name='unique_user_stat_type_stat'),)

class Survey(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    survey_type = db.Column(db.String(50), nullable=False)  # 
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=False)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class UserSurvey(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    survey_id = db.Column(db.Integer, db.ForeignKey('survey.id'), nullable=False)
    completed = db.Column(db.Boolean, default=False)
    completed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # UserSurvey
    user = db.relationship('User', backref=db.backref('user_surveys', lazy=True))
    survey = db.relationship('Survey', backref=db.backref('participants', lazy=True))
    
    __table_args__ = (db.UniqueConstraint('user_id', 'survey_id', name='unique_user_survey'),)

class ShopItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)  #  "dayhub_body"
    unlockid = db.Column(db.BigInteger, nullable=False)  # ID
    unlock_cost = db.Column(db.Integer, nullable=False)  # 
    currency_type = db.Column(db.String(50), nullable=False)  #  "heart"
    listed = db.Column(db.Boolean, default=True)  # 
    unlock_dep = db.Column(db.Integer, default=0)  # 
    visual_dep = db.Column(db.Integer, default=0)  # 
    x = db.Column(db.Integer, default=0)  # X
    y = db.Column(db.Integer, default=0)  # Y
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # ID
    __table_args__ = (db.UniqueConstraint('unlockid', name='unique_unlockid'),)

class ConsumableDef(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    consumable_id = db.Column(db.BigInteger, nullable=False, unique=True)  # ID
    name = db.Column(db.String(100), nullable=False)  # 
    buff_id = db.Column(db.BigInteger, default=0)  # ID
    cooldown = db.Column(db.Float, default=0.0)  # 
    icon = db.Column(db.String(100), nullable=False)  # 
    event = db.Column(db.String(50), default='none')  # 
    is_toy = db.Column(db.Boolean, default=False)  # 
    type = db.Column(db.String(100), default='')  # 
    param = db.Column(db.String(100), default='')  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (db.UniqueConstraint('consumable_id', name='unique_consumable_id'),)

class UserConsumable(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    consumable_id = db.Column(db.BigInteger, nullable=False)  # ID
    cooldown_until = db.Column(db.BigInteger, default=0)  # 
    quantity = db.Column(db.Integer, default=0)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('consumables', lazy=True))
    
    __table_args__ = (db.UniqueConstraint('user_id', 'consumable_id', name='unique_user_consumable'),)

class Lootbox(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    lootbox_id = db.Column(db.String(100), nullable=False)  # ID
    lootbox_type = db.Column(db.String(50), nullable=False)  # 
    quantity = db.Column(db.Integer, default=1)  # 
    acquired_at = db.Column(db.Integer, nullable=False)  # 
    expires_at = db.Column(db.Integer, nullable=True)  # 
    opened = db.Column(db.Boolean, default=False)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('lootboxes', lazy=True))

class GenericShopDef(db.Model):
    id = db.Column(db.BigInteger, primary_key=True)  # BigIntegerID
    shop_name = db.Column(db.String(100), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    icon = db.Column(db.String(255), default="")
    localized_name = db.Column(db.String(255), default="")
    order_index = db.Column(db.Integer, default=-1)
    cost = db.Column(db.Integer, nullable=False)
    currency_type = db.Column(db.String(50), nullable=False)
    event_name = db.Column(db.String(100), default="")
    max_per_cycle = db.Column(db.Integer, default=1)
    reset_event = db.Column(db.String(100), default="")
    item_type = db.Column(db.String(50), nullable=False)
    item_name = db.Column(db.String(100), nullable=False)
    item_count = db.Column(db.Integer, default=1)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class ShopCostCurrency(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.BigInteger, db.ForeignKey('generic_shop_def.id'), nullable=False)
    cost = db.Column(db.Integer, nullable=False)
    currency_type = db.Column(db.String(50), nullable=False)
    index = db.Column(db.Integer, nullable=False)  # 
    
    # GenericShopDef
    shop_def = db.relationship('GenericShopDef', backref=db.backref('costs_currencies', lazy=True))

class ShopUnlockRequirement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    shop_id = db.Column(db.BigInteger, db.ForeignKey('generic_shop_def.id'), nullable=False)
    requirement = db.Column(db.String(100), nullable=False)
    index = db.Column(db.Integer, nullable=False)  # 
    
    # GenericShopDef
    shop_def = db.relationship('GenericShopDef', backref=db.backref('unlock_requirements', lazy=True))

class ExternalAccountFriend(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    external_account_id = db.Column(db.String(100), nullable=False)  # ID
    external_platform = db.Column(db.String(50), nullable=False)  # 
    friend_user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=True)  # ID
    friend_nickname = db.Column(db.String(100), nullable=False)  # 
    friend_avatar = db.Column(db.String(255), nullable=True)  # URL
    is_connected = db.Column(db.Boolean, default=False)  # 
    connected_at = db.Column(db.Integer, nullable=True)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('external_friends', lazy=True))
    friend_user = db.relationship('User', foreign_keys=[friend_user_id])

class SerendipityMatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    matched_user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    match_type = db.Column(db.String(50), nullable=False)  # 
    match_score = db.Column(db.Float, default=0.0)  # 
    matched_at = db.Column(db.Integer, nullable=False)  # 
    expires_at = db.Column(db.Integer, nullable=True)  # 
    status = db.Column(db.String(20), default='active')  # active, expired, used
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('serendipity_matches', lazy=True))
    matched_user = db.relationship('User', foreign_keys=[matched_user_id])

class StarTagDef(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)  # 
    sku = db.Column(db.String(100), nullable=False)  # SKU
    part = db.Column(db.String(100), default="")  # 
    consumable_name = db.Column(db.String(100), nullable=False)  # 
    group_name = db.Column(db.String(100), default="")  # 
    batches = db.Column(db.String(500), default="")  # JSON
    can_link_with = db.Column(db.String(100), default="")  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class StarTagLink(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    tag_name = db.Column(db.String(100), nullable=False)  # 
    linked_item = db.Column(db.String(100), nullable=False)  # 
    linked_at = db.Column(db.Integer, nullable=False)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('star_tag_links', lazy=True))

class FreeGift(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    receiver_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    gift_type = db.Column(db.String(50), nullable=False)  # 
    gift_id = db.Column(db.String(100), nullable=False)  # ID
    sent_at = db.Column(db.Integer, nullable=False)  # 
    received = db.Column(db.Boolean, default=False)  # 
    received_at = db.Column(db.Integer, nullable=True)  # 
    expires_at = db.Column(db.Integer, nullable=True)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    sender = db.relationship('User', foreign_keys=[sender_id], backref=db.backref('sent_gifts', lazy=True))
    receiver = db.relationship('User', foreign_keys=[receiver_id], backref=db.backref('received_gifts', lazy=True))

class MapDefinition(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    def_type = db.Column(db.String(50), nullable=False)  # : wing_buff, collectible
    name = db.Column(db.String(100), nullable=False)     # 
    level = db.Column(db.String(100), nullable=False)    # 
    landmark = db.Column(db.String(100), nullable=True)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (db.UniqueConstraint('def_type', 'name', 'level', name='unique_map_definition'),)

class VideoDefinition(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)  # 
    duration = db.Column(db.Integer, nullable=False)  # 
    location = db.Column(db.Text, nullable=False)  # URL
    subtitle_base = db.Column(db.Text, default='')  # URL
    has_translations = db.Column(db.Boolean, default=False)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class VideoSchedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    channel = db.Column(db.String(100), nullable=False)  # 
    event = db.Column(db.String(100), nullable=False)  # 
    preshow_seconds = db.Column(db.Integer, default=0)  # 
    playlist = db.Column(db.Text, nullable=False)  # JSON
    loop = db.Column(db.Boolean, default=False)  # 
    gap_seconds = db.Column(db.Integer, default=0)  # 
    postshow_seconds = db.Column(db.Integer, default=0)  # 
    silence_please = db.Column(db.Boolean, default=False)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserOutfit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False, unique=True)
    wing_tex = db.Column(db.Integer, default=0)
    wing_pat = db.Column(db.Integer, default=0)
    wing_mask = db.Column(db.Integer, default=0)
    wing_id = db.Column(db.BigInteger, default=2245279351)
    voice = db.Column(db.Integer, default=9)
    seed = db.Column(db.Integer, default=16515)
    scale = db.Column(db.Float, default=-0.007529020463710151)
    refreshversion = db.Column(db.Integer, default=0)
    prop_tex = db.Column(db.Integer, default=0)
    prop_pat = db.Column(db.Integer, default=0)
    prop_mask = db.Column(db.Integer, default=0)
    prop_id = db.Column(db.BigInteger, default=2035109393)
    neck_tex = db.Column(db.Integer, default=0)
    neck_pat = db.Column(db.Integer, default=0)
    neck_mask = db.Column(db.Integer, default=0)
    neck_id = db.Column(db.BigInteger, default=3800884691)
    mask_tex = db.Column(db.Integer, default=0)
    mask_pat = db.Column(db.Integer, default=0)
    mask_mask = db.Column(db.Integer, default=0)
    mask_id = db.Column(db.BigInteger, default=3663390882)
    horn_tex = db.Column(db.Integer, default=0)
    horn_pat = db.Column(db.Integer, default=0)
    horn_mask = db.Column(db.Integer, default=0)
    horn_id = db.Column(db.BigInteger, default=3680499229)
    height = db.Column(db.Float, default=1.9818411339409865)
    hat_tex = db.Column(db.Integer, default=0)
    hat_pat = db.Column(db.Integer, default=0)
    hat_mask = db.Column(db.Integer, default=0)
    hat_id = db.Column(db.BigInteger, default=1470462579)
    hair_tex = db.Column(db.Integer, default=0)
    hair_pat = db.Column(db.Integer, default=0)
    hair_mask = db.Column(db.Integer, default=0)
    hair_id = db.Column(db.BigInteger, default=1229053584)  # CharSkyKid_Hair_BraidSideSmall (初始头发)
    feet_tex = db.Column(db.Integer, default=0)
    feet_pat = db.Column(db.Integer, default=0)
    feet_mask = db.Column(db.Integer, default=0)
    feet_id = db.Column(db.BigInteger, default=1532093990)
    face_tex = db.Column(db.Integer, default=0)
    face_pat = db.Column(db.Integer, default=0)
    face_mask = db.Column(db.Integer, default=0)
    face_id = db.Column(db.BigInteger, default=4267756678)  # CharSkyKid_Mask_None (无面具; 原长者面具 ElderRain_02)
    body_tex = db.Column(db.Integer, default=0)
    body_pat = db.Column(db.Integer, default=0)
    body_mask = db.Column(db.Integer, default=0)
    body_id = db.Column(db.BigInteger, default=3691343236)
    attitude = db.Column(db.String(10), default="0")
    arms_id = db.Column(db.Integer, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('outfit', uselist=False))

class LevelPickup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    level = db.Column(db.BigInteger, nullable=False)  # ID
    emitter_name = db.Column(db.String(100), nullable=False)  # 
    event_name = db.Column(db.String(100), nullable=False)  # 
    preactive_duration = db.Column(db.Integer, default=0)
    pool_name = db.Column(db.String(100), nullable=False)
    pool_amount = db.Column(db.Integer, default=0)
    last_chunk_seq = db.Column(db.Integer, default=-1)
    emit_interval = db.Column(db.Integer, default=0)
    currency_type = db.Column(db.String(50), default="wax")
    collected = db.Column(db.Integer, default=0)
    chunks_per_emit = db.Column(db.Integer, default=1)
    chunk_size_likelihood = db.Column(db.Text)  # JSON
    chunk_size_amount = db.Column(db.Text)  # JSON
    active_duration = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('level_pickups', lazy=True))
    
    __table_args__ = (db.UniqueConstraint('user_id', 'level', 'emitter_name', 'event_name', name='unique_user_level_pickup'),)

class ServerAssignment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    server_uri = db.Column(db.String(255), nullable=False)  # 
    move_ts = db.Column(db.Integer, nullable=False)  # 
    signature = db.Column(db.String(64), nullable=False)  # 
    sig = db.Column(db.String(64), nullable=False)  # 
    ps_only = db.Column(db.Boolean, default=False)  # PlayStation
    platform_filter = db.Column(db.Integer, default=0)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('server_assignments', lazy=True))

class GameServer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    server_uri = db.Column(db.String(255), nullable=False, unique=True)  # 
    region = db.Column(db.String(50), nullable=False)  # 
    capacity = db.Column(db.Integer, default=100)  # 
    current_players = db.Column(db.Integer, default=0)  # 
    is_active = db.Column(db.Boolean, default=True)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ForgeRate - 
class ForgeRate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    source_type = db.Column(db.String(50), nullable=False)  #  "wax"
    current_tier = db.Column(db.Integer, default=1)  # 
    current_level = db.Column(db.Integer, default=1)  # 
    tier_completion = db.Column(db.Float, default=0.0)  # 
    level_completion = db.Column(db.Float, default=0.0)  # 
    level_tier_index = db.Column(db.Integer, default=0)  # 
    level_tier_count = db.Column(db.Integer, default=5)  # 
    reset_acknowledged = db.Column(db.Boolean, default=False)  # 
    enabled = db.Column(db.Boolean, default=True)  # 
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('forge_rates', lazy=True))
    
    __table_args__ = (db.UniqueConstraint('user_id', 'source_type', name='unique_user_currency'),)

class TrustGroupDef(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.String(100), nullable=False, unique=True)  # ID
    group_name = db.Column(db.String(255), nullable=False)  # 
    description = db.Column(db.Text, nullable=True)  # 
    min_trust_level = db.Column(db.Integer, default=0)  # 
    max_members = db.Column(db.Integer, default=50)  # 
    is_active = db.Column(db.Boolean, default=True)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class UserTrustGroup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    group_id = db.Column(db.String(100), db.ForeignKey('trust_group_def.group_id'), nullable=False)
    trust_level = db.Column(db.Integer, default=1)  # 
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)  # 
    
    # UserTrustGroupDef
    user = db.relationship('User', backref=db.backref('trust_groups', lazy=True))
    group = db.relationship('TrustGroupDef', backref=db.backref('members', lazy=True))
    
    __table_args__ = (db.UniqueConstraint('user_id', 'group_id', name='unique_user_trust_group'),)

class UserCurrency(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    candles = db.Column(db.Integer, default=0)
    heart = db.Column(db.Integer, default=0)
    heart_wax = db.Column(db.Integer, default=0)
    prestige = db.Column(db.Integer, default=0)
    prestige_vip = db.Column(db.Integer, default=0)
    prestige_wax = db.Column(db.Integer, default=0)
    season_candle = db.Column(db.Integer, default=0)
    season_wax = db.Column(db.Integer, default=0)
    season_wax_14 = db.Column(db.Integer, default=0)
    season_heart = db.Column(db.Integer, default=0)  # 季节爱心
    # 季卡券(国际服 currency 键名 season_pass_token)。内购 SPASSG 等商品发它,
    # 客户端季节面板用它显示"季卡持有/可赠送"。旧库由 database/migrate.py 补列。
    season_pass_token = db.Column(db.Integer, default=0)
    vip = db.Column(db.Integer, default=0)
    wax = db.Column(db.Integer, default=3)  # 白蜡默认3个
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('currency', lazy=True))

class SeasonUnlock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    unlock_type = db.Column(db.String(50), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.Integer, default=0)
    ack = db.Column(db.Boolean, default=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('season_unlocks', lazy=True))
    
    __table_args__ = (db.UniqueConstraint('user_id', 'name', name='unique_user_season_unlock'),)

class FollowRelationship(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)  # ID
    following_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)  # ID
    status = db.Column(db.String(20), default='active')  # : active, blocked, pending
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    follower = db.relationship('User', foreign_keys=[follower_id], backref=db.backref('following_relationships', lazy=True))
    following = db.relationship('User', foreign_keys=[following_id], backref=db.backref('follower_relationships', lazy=True))
    
    __table_args__ = (db.UniqueConstraint('follower_id', 'following_id', name='unique_follow_relationship'),)

class Stage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    stage_id = db.Column(db.String(100), nullable=False)  # ID "CandleSpace_CNDL"
    level_id = db.Column(db.String(100), nullable=False)  # ID "CandleSpace"
    name = db.Column(db.String(200))  # 
    description = db.Column(db.Text)  # 
    status = db.Column(db.String(50), default="active")  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # IDID
    __table_args__ = (db.UniqueConstraint('stage_id', 'level_id', name='unique_stage_level'),)

class SeasonQuest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.String(10), nullable=False)  # : YYYY-MM-DD
    daily_quest_def_id = db.Column(db.String(50), nullable=False)
    stat_type = db.Column(db.String(50), nullable=False)
    start_value = db.Column(db.Float, default=-1.0)
    activated = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('season_quests', lazy=True))
    
    __table_args__ = (db.UniqueConstraint('user_id', 'date', 'daily_quest_def_id', name='unique_user_daily_quest'),)

class SeasonQuestReward(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    date = db.Column(db.String(10), nullable=False)  # : YYYY-MM-DD
    daily_quest_def_id = db.Column(db.String(50), nullable=False)
    rewarded = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('season_quest_rewards', lazy=True))
    
    __table_args__ = (db.UniqueConstraint('user_id', 'date', 'daily_quest_def_id', name='unique_user_daily_quest_reward'),)

class TrustActivityDef(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    activity_id = db.Column(db.String(100), unique=True, nullable=False)  # ID
    activity_name = db.Column(db.String(200), nullable=False)  # 
    description = db.Column(db.Text)  # 
    activity_type = db.Column(db.String(50), nullable=False)  # 
    required_level = db.Column(db.Integer, default=1)  # 
    reward_points = db.Column(db.Integer, default=0)  # 
    is_active = db.Column(db.Boolean, default=True)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class PlayerQuest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    quest_name = db.Column(db.String(100), nullable=False)
    quest_id = db.Column(db.String(36), nullable=False, unique=True)
    quest_status = db.Column(db.String(20), nullable=False)  # inactive, active, failed, succeeded, collected, canceled
    start_time = db.Column(db.Integer, nullable=False)
    expiry_time = db.Column(db.Integer, nullable=False)
    status = db.Column(db.String(50), default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('player_quests', lazy=True))

class QuestStat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    quest_id = db.Column(db.String(36), db.ForeignKey('player_quest.quest_id'), nullable=False)
    stat_type = db.Column(db.String(100), nullable=False)
    stat_category = db.Column(db.String(50), nullable=False)  # init_stats, completion_requirements, target_values
    value = db.Column(db.BigInteger, nullable=False)
    updated = db.Column(db.String(50), nullable=True)
    updated_epoch = db.Column(db.BigInteger, nullable=True)
    
    # PlayerQuest
    quest = db.relationship('PlayerQuest', backref=db.backref('quest_stats', lazy=True))

class ResourceToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    token = db.Column(db.Text, nullable=False)
    host = db.Column(db.String(255), nullable=False, default='sky.resources.cdn.thatgamecompany.com')
    prefix = db.Column(db.String(255), default='')
    status = db.Column(db.String(50), default='OK')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    
    # User
    user = db.relationship('User', backref=db.backref('resource_tokens', lazy=True))

class UserRank(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False, unique=True)
    social_rank = db.Column(db.Float, default=0.0)  # 
    is_shepherd = db.Column(db.Boolean, default=False)  # 
    is_beginner = db.Column(db.Boolean, default=False)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('rank', uselist=False))

class Achievement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    achievement_id = db.Column(db.String(100), nullable=False)  # ID
    achievement_name = db.Column(db.String(255), nullable=False)  # 
    achievement_description = db.Column(db.Text)  # 
    unlocked_at = db.Column(db.Integer, nullable=False)  # 
    progress = db.Column(db.Integer, default=0)  # 
    completed = db.Column(db.Boolean, default=False)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('achievements', lazy=True))
    
    __table_args__ = (db.UniqueConstraint('user_id', 'achievement_id', name='unique_user_achievement'),)

class EventSchedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)  # 
    duration = db.Column(db.String(50), nullable=False)  # 
    when = db.Column(db.Text, nullable=False)  # JSON
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (db.UniqueConstraint('name', name='unique_event_name'),)

# IAP
class IAPProduct(db.Model):
    id = db.Column(db.String(50), primary_key=True)  # ID "SPASSR"
    product_type = db.Column(db.String(50), nullable=False)  #  "Consumable"
    tier = db.Column(db.Integer, nullable=False)  # 
    name = db.Column(db.String(255), nullable=False)  # 
    description = db.Column(db.Text)  # 
    icon = db.Column(db.String(255))  # 
    sound_id = db.Column(db.Integer)  # ID
    currency_type = db.Column(db.String(50))  # 1
    currency_count = db.Column(db.Integer)  # 1
    currency_type2 = db.Column(db.String(50))  # 2
    currency_count2 = db.Column(db.Integer)  # 2
    unlocks = db.Column(db.Text)  # JSON
    platforms = db.Column(db.String(255))  # 
    giftable = db.Column(db.Boolean, default=False)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class UnlockItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    unlock_type = db.Column(db.String(50), nullable=False)
    ack = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.Integer, nullable=False)
    
    # User
    user = db.relationship('User', backref=db.backref('unlock_items', lazy=True))
    
    __table_args__ = (db.UniqueConstraint('user_id', 'name', name='unique_user_unlock_item'),)

class Collectible(db.Model):
    # 代理主键(自增): 原来用 id(收藏品名)做主键, 导致"cape"等全表唯一,
    # 多个用户无法各自拥有同名收藏品(UNIQUE constraint failed: collectible.id)。
    # 改为自增主键 + UNIQUE(user_id, id), 每个用户可各自拥有同一收藏品。
    row_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    id = db.Column(db.String(100), nullable=False)  # 收藏品名 "cape","point" 等
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    used = db.Column(db.Boolean, default=False)  #
    candle_space = db.Column(db.Boolean, default=False)  #
    carrying = db.Column(db.Boolean, default=False)  #
    level = db.Column(db.Integer, default=1)  #
    permanent_level = db.Column(db.Integer, default=0)  #
    # 用 FlexibleDateTime: 面板"推送全动作"是裸 SQL 写入的, 这两列在库里是 int 秒,
    # 声明成 db.DateTime 会让 get_collectibles 读库时抛 fromisoformat -> 接口 500
    # (2026-10-01 玩家反馈"接口到某个就连接失败")。见 FlexibleDateTime 的说明。
    created_at = db.Column(FlexibleDateTime, default=datetime.utcnow)
    updated_at = db.Column(FlexibleDateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow)

    # User
    user = db.relationship('User', backref=db.backref('collectibles', lazy=True))

    __table_args__ = (db.UniqueConstraint('user_id', 'id', name='unique_user_collectible'),)


class AppBadge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    badge_number = db.Column(db.Integer, default=0)  # 
    badge_type = db.Column(db.String(50), default='general')  # 
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('app_badges', lazy=True))
    
    __table_args__ = (db.UniqueConstraint('user_id', 'badge_type', name='unique_user_badge_type'),)

# buff
class UserBuff(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    buff_id = db.Column(db.String(50), nullable=False)  # buff
    buff_type = db.Column(db.String(50), nullable=False)  # buff
    value = db.Column(db.Float, default=0.0)  # buff
    duration = db.Column(db.Integer, default=0)  # ()
    expires_at = db.Column(db.DateTime, nullable=True)  # 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # User
    user = db.relationship('User', backref=db.backref('buffs', lazy=True))
    
    # buff
    __table_args__ = (db.UniqueConstraint('user_id', 'buff_id', name='unique_user_buff'),)

class UserCheckpoint(db.Model):
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), primary_key=True)
    checkpoints = db.Column(db.Text, default='[]')  # 存储为JSON字符串
    
    def __repr__(self):
        return f'<UserCheckpoint {self.user_id}>'
        
class UserPickup(db.Model):
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), primary_key=True)
    pickups = db.Column(db.Text, default='[]')  # 存储为JSON字符串
    
    def __repr__(self):
        return f'<UserPickup {self.user_id}>'

class UserUnlocks(db.Model):
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), primary_key=True)
    unlocks = db.Column(db.Text, default='[]')  # 存储为JSON字符串
    
    def __repr__(self):
        return f'<UserUnlocks {self.user_id}>'


class UserWingBuff(db.Model):    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    collected = db.Column(db.Boolean, default=False)
    deposited = db.Column(db.Boolean, default=False)
    last_conversion = db.Column(db.BigInteger, default=0)
    deposit_id = db.Column(db.BigInteger, default=0)
    created_at = db.Column(db.Integer, default=lambda: int(time.time()))
    updated_at = db.Column(db.Integer, default=lambda: int(time.time()), onupdate=lambda: int(time.time()))
    
    __table_args__ = (
        db.UniqueConstraint('user_id', 'name', name='uq_user_wing_buff'),
    )
    
class UserInitialization(db.Model):
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False, unique=True, index=True)
    is_initialized = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.Integer, default=lambda: int(time.time()))
    updated_at = db.Column(db.Integer, default=lambda: int(time.time()), onupdate=lambda: int(time.time()))
    
    def __repr__(self):
        return f'<UserInitialization {self.user_id}:{self.is_initialized}>'

class Message(db.Model):
    __tablename__ = 'message'
    
    id = db.Column(db.String(255), primary_key=True)  # 消息ID，与JSON文件中的id对应
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), primary_key=True)  # 用户ID，复合主键
    received_at = db.Column(db.Integer, nullable=True)  # 接收时间戳
    
    created_at = db.Column(db.Integer, default=lambda: int(time.time()))
    updated_at = db.Column(db.Integer, default=lambda: int(time.time()))
    
    def __repr__(self):
        return f'<Message {self.id} for user {self.user_id}>'

class SocialFeedPost(db.Model):
    __tablename__ = 'social_feed_post'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    # 改为纯 UUID 字符串，与 get 接口示例一致
    social_feed_id = db.Column(db.String(36), nullable=False, unique=True, default=lambda: str(uuid.uuid4()))
    pool_type = db.Column(db.String(50), nullable=False)
    pool_name = db.Column(db.String(100), nullable=False)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.Integer, nullable=False, default=lambda: int(time.time()))
    expire_at = db.Column(db.Integer, nullable=False, default=lambda: int(time.time()) + 1209600)  # 14天过期
    
    # 可选统计字段（为 reactions 准备）
    likes_count = db.Column(db.Integer, default=0)
    comments_count = db.Column(db.Integer, default=0)
    report_count = db.Column(db.Integer, default=0)
    hide_count = db.Column(db.Integer, default=0)
    attempt_count = db.Column(db.Integer, default=0)
    succeed_count = db.Column(db.Integer, default=0)
    is_poisoned = db.Column(db.Boolean, default=False)
    
    user = db.relationship('User', backref=db.backref('social_posts', lazy=True))
    
    def __repr__(self):
        return f'<SocialFeedPost {self.social_feed_id}>'

class SocialFeedLike(db.Model):
    __tablename__ = 'social_feed_like'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), nullable=False)
    social_feed_id = db.Column(db.String(36), db.ForeignKey('social_feed_post.social_feed_id'), nullable=False)
    created_at = db.Column(db.Integer, nullable=False, default=lambda: int(time.time()))
    
    __table_args__ = (
        db.UniqueConstraint('user_id', 'social_feed_id', name='unique_user_feed_like'),
    )
    
    user = db.relationship('User', backref=db.backref('feed_likes', lazy=True))
    post = db.relationship('SocialFeedPost', backref=db.backref('likes', lazy=True))
    
    def __repr__(self):
        return f'<SocialFeedLike user:{self.user_id} feed:{self.social_feed_id}>'

class UserData(db.Model):
    """玩家存档(userdata)持久化。

    客户端通过 /account/sync_user_data 上传整份存档(base64(JSON) 放在 data 字段),
    通过 /account/userdata 拉取。之前服务端两个接口都忽略上传内容、永远返回同一份
    写死的静态 blob, 导致:
      - 每日任务的 revealedQuestTimes / daily_quests_activated /
        daily_quests_interaction_epoch_time 永远是 0/false -> 向导每日任务的
        互动状态不持久化, 点互动(击掌等)后状态存不下来、下次又被清零;
      - 教程进度/常用表情等所有存档都不保存。
    本表按 user_id 存最近一次上传的 data(及 time/version), 实现真正的存档持久化。
    """
    __tablename__ = 'user_data'

    user_id = db.Column(db.String(36), db.ForeignKey('user.id'), primary_key=True)
    data = db.Column(db.Text, nullable=False, default='')   # base64(JSON) 原样存
    data_time = db.Column(db.Integer, default=0)            # 客户端上传的 time
    data_version = db.Column(db.Integer, default=3)         # 客户端上传的 version
    updated_at = db.Column(db.Integer, default=lambda: int(time.time()),
                           onupdate=lambda: int(time.time()))

    user = db.relationship('User', backref=db.backref('userdata_row', uselist=False, lazy=True))

    def __repr__(self):
        return f'<UserData user:{self.user_id} v{self.data_version}>'
