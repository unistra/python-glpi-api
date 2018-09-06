.. glpi-api documentation master file, created by
   sphinx-quickstart on Mon Sep  3 16:17:24 2018.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

python-glpi-api
===============

.. automodule:: glpi_api

Helpers
-------
.. autofunction:: connect

API
---
.. autoclass:: GLPI
    :members: kill_session,
              get_my_profiles, get_active_profile, set_active_profile,
              get_my_entities, get_active_entity, set_active_entity,
              get_full_session, get_config,
              get_item, get_all_items, get_sub_items, get_multiple_items,
              list_search_options, field_id, field_uid, search,
              add, update, delete
    :member-order: bysource
