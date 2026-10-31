# -*- coding: utf-8 -*-
"""
Service模块: stage
"""

from .get import get_stage
# 共享空间: 保存摆放(set) + 公寓进出(apartment) —— 原来都没注册, 见各自文件头说明
from .set import set_stage
from .apartment import (friends_in_apartments, leave_apartment,
                        update_apartment_status)

__all__ = ['get_stage', 'set_stage', 'friends_in_apartments',
           'leave_apartment', 'update_apartment_status']
