"""giftsjourneyrebirth 店铺经营协作边界

业务事实在 `scope.py`（接线）和 `vocabulary.py`（共享词汇），
机制在 `core/`。
"""

from __future__ import annotations

from fulfillment_scopes.shopify.scope import SCOPE as SHOPIFY_STORE_SCOPE

__all__ = ["SHOPIFY_STORE_SCOPE"]
