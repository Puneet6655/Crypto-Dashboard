"""Accounts, sign-in and roles.

Roles, from least to most access:

* viewer   every new sign-up. Can use the dashboard: run lookups, read cases, status and sources.
* analyst  also creates cases and adds addresses and transactions to them.
* admin    also edits the sources list, runs BigQuery tools, deletes cases and manages users.

The first admin is created from the command line, never from the browser:
    python -m app.auth create-admin you@example.com
"""
