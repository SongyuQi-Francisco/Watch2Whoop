"""Avatars people upload themselves (#200, behind the `avatars` feature flag): a personal picture and a circle's logo.

- params.py: upload rate limit
- store.py: set / clear a user's or a circle's picture, who may see which, the URLs the other pages embed
- api.py: PUT / DELETE /v1/avatars/me, PUT / DELETE /v1/avatars/circles/{id} (owner), GET /v1/avatars/users/{id}
Image checks, square crop, resize and metadata removal are images/ (shared with the admin's circle avatars, #199).
A personal avatar is visible to the person and to members of a circle they are in; a circle's logo to its members.
The flag only gates uploading and deleting: pictures that exist (e.g. a circle avatar the admin set) are shown regardless.
"""
