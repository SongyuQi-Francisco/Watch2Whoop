"""The inbox behind 「我 → 消息」 (issue #359 P1, flag `inbox`): one list merged read-only from admin notices, the weekly
what's new, circle invites addressed to the person and circle notifications, plus one-step invite cards.

params.py  how long items stay, page sizes, the labels of what a circle shares
store.py   the merge, read / hidden state, accept / ignore an invite (joining is friends/store.join)
api.py     /v1/users/{user}/inbox… (own only)
"""
