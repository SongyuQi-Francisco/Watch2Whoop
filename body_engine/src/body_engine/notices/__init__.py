"""Messages from the admin to one user (migration 775): a one-time sheet on the Today page with an optional button link.

store.py   send / list / pending / mark seen / revoke, link check, who is an admin (BODY_ADMIN_USER_IDS)
api.py     GET /v1/users/{user}/notices, POST …/notices/{id}/seen (own only); /v1/admin/notices (admins only)
CLI        body-engine notice-send / notice-list / notice-revoke (cli.py)
Page       web/static/notices.js
"""
