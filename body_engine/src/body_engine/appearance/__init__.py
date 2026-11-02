"""Per-person look of the Today page: theme (default / slim) and the daily quote switch (issue #126, flag slim_mode, migration 900).

The theme itself is CSS variables in web/static/slim.js; the quotes are web/static/slim-quotes.js. This package only keeps the
choice on the server, so it follows the account to the next device."""
