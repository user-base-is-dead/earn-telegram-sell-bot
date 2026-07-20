"""Conversation state constants shared by every handler module.

Keeping every state number in one place avoids two ConversationHandlers
accidentally reusing the same int for unrelated states.
"""

PAY_UTR = 0
ADD_NAME, ADD_DESC, ADD_PRICE, ADD_STOCK, ADD_ICON = range(1, 6)
REJECT_REASON = 10
EDIT_VALUE = 11
APPROVE_DELIVER = 12
CLEAN_CONFIRM = 13
BC_COMPOSE, BC_PRODUCT, BC_REVIEW = range(20, 23)
TOPUP_AMOUNT, TOPUP_CHECK = range(30, 32)
KEYS_INPUT = 32
BUY_QTY = 33
DISCOUNT_PRICE, DISCOUNT_DURATION = range(34, 36)

# Add-product wizard: the ordered steps used by _wizard_prompt/add_back to walk
# forward / backward through the steps.
WIZARD_ORDER = (ADD_NAME, ADD_DESC, ADD_PRICE, ADD_STOCK, ADD_ICON)
