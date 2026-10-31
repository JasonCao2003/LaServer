# -*- coding: utf-8 -*-
"""
Service模块: message
"""

from .get_all import get_all_messages_v2
from .get_allv1 import get_all_messages_v1
from .mark_received import mark_received

__all__ = ['get_all_messages_v2', 'mark_received', 'get_all_messages_v1']
