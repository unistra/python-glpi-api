# coding: utf-8

"""Module for interacting with GLPI using the REST API. It just wraps endpoints
provided by the API and manage HTTP return codes.
"""

from __future__ import unicode_literals
import re
import os
import sys
import warnings
from functools import wraps
from base64 import b64encode
from contextlib import contextmanager
import requests

_UPLOAD_MANIFEST = '{{ "input": {{ "name": "{name:s}", "_filename" : ["{filename:s}"] }} }}'
"""Manifest when uploading a document passed as JSON in the multipart/form-data POST
request. Note the double curly is used for representing only one curly."""

_WARN_DEL_DOC = (
    "The file could not be uploaded but a document with id '{:d}' was created, "
    "this document will be purged.")
"""Warning when we need to delete an incomplete document due to upload error."""

_WARN_DEL_ERR = (
    "The created document could not be purged, you may need to cealn it manually: {:s}")
"""Warning when an invalid document could not be purged."""

_FILENAME_RE = re.compile('^filename="(.+)";')

class GLPIError(Exception):
    """Exception raised by this module."""

@contextmanager
def connect(url, apptoken, auth, verify_certs=True, use_headers=True, user_agent=None):
    """Context manager that authenticate to GLPI when enter and kill application
    session in GLPI when leaving:

    .. code::

        >>> import glpi_api
        >>>
        >>> URL = 'https://glpi.exemple.com/apirest.php'
        >>> APPTOKEN = 'YOURAPPTOKEN'
        >>> USERTOKEN = 'YOURUSERTOKEN'
        >>>
        >>> try:
        >>>     with glpi_api.connect(URL, APPTOKEN, USERTOKEN) as glpi:
        >>>         print(glpi.get_config())
        >>> except glpi_api.GLPIError as err:
        >>>     print(str(err))

    You can set ``verify_certs`` to *False* to ignore invalid SSL certificates.

    ``use_headers`` indicates whether authentication parameters are sent through HTTP
    headers or as GET parameters (in the URL). The default is to use headers but
    some environments (cf `this GLPI issue
    <https://github.com/glpi-project/glpi/issues/5116#issuecomment-496166674>`_ and
    the following Stack Overflow post) may require to use GET parameters.
    """
    glpi = GLPI(url, apptoken, auth, verify_certs, use_headers=use_headers)
    try:
        yield glpi
    finally:
        glpi.kill_session()

def _raise(msg):
    """Raise ``GLPIError`` exception with ``msg`` message.

    In Python 2, exceptions expect ``str`` by default. ``requests`` module
    returns unicode strings and ``__future__.unicode_literals`` is used for
    ensuring all strings are ``unicode`` (prevent the use of ``u''`` and
    make strings manipulations easier). So for Python 2 we need to encode
    to ``str`` the message.
    """
    if sys.version_info.major < 3:
        msg = msg.encode('utf-8')
    raise GLPIError(msg)

def _glpi_error(response):
    """GLPI errors message are returned in a list of two elements. The first
    element is the key of the error and the second the message."""
    _raise('({}) {}'.format(*response.json()))

def _unknown_error(response):
    """Helper for returning a HTTP code and response on non managed status
    code."""
    _raise('unknown error: [{:d}/{:s}] {:s}'
           .format(response.status_code, response.reason, response.text))

def _convert_bools(kwargs):
    return {key: str(val).lower() if isinstance(val, bool) else val
            for key, val in kwargs.items()}

def _catch_errors(func):
    """Decorator function for catching communication error
    and raising an exception."""
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except requests.exceptions.RequestException as err:
            raise GLPIError('communication error: {:s}'.format(str(err)))
    return wrapper

class GLPI:
    """Class for interacting with GLPI using the REST API.

    The constructor authenticate to the GLPI platform at ``url`` using an
    application token ``apptoken`` (see API clients configuration) and either a
    string containing the user token or a couple of username/password as ``auth``
    parameter:

    .. code::

       # Authentication using user API token.
       glpi = GLPI(url='https://glpi.exemple.com/apirest.php',
                   apptoken='YOURAPPTOKEN',
                   auth='YOURUSERTOKEN')
       # Authentication using username/password.
       glpi = GLPI(url='https://glpi.exemple.com/apirest.php',
                   apptoken='YOURAPPTOKEN',
                   auth=('USERNAME', 'PASSWORD'))

    `verify_certs` and `use_headers` can be unset to respectively not checking
    SSL certificates and passing authentication parameters as GET parameters
    (instead of headers).
    """
    def __init__(self, url, apptoken, auth, verify_certs=True, use_headers=True,
                 user_agent=None):
        """Connect to GLPI and retrieve session token which is put in a
        ``requests`` session as attribute.
        """
        self.url = url

        # Initialize session.
        self.session = requests.Session()
        if not verify_certs:
            from requests.packages.urllib3.exceptions import InsecureRequestWarning
            requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
            self.session.verify = False

        # Connect and retrieve token.
        session_token = self._init_session(apptoken, auth, user_agent,
                                           use_headers=use_headers)

        # Set required headers.
        headers = {
            'Content-Type': 'application/json',
            'Session-Token': session_token,
            'App-Token': apptoken
        }
        if user_agent:
            headers['User-Agent'] = user_agent
        self.session.headers = headers

        # Use for caching field id/uid map.
        self._fields = {}

    def _set_method(self, *endpoints):
        """Generate the URL from ``endpoints``."""
        return '/'.join(str(part) for part in [self.url.strip('/'), *endpoints])

    @_catch_errors
    def _init_session(self, apptoken, auth, user_agent, use_headers=True):
        """API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#init-session>`__

        Request a session token (will be sent alongside `apptoken` for next API calls).
        ``auth`` can either be a string containing the user token of a list/tuple
        of two elements containing username and password.
        """
        init_headers = {
            'Content-Type': 'application/json',
            'App-Token': apptoken
        }
        if user_agent:
            init_headers['User-Agent'] = user_agent
        params = {}

        if isinstance(auth, (list, tuple)):
            if len(auth) > 2:
                raise GLPIError("invalid 'auth' parameter (should contains "
                                'username and password)')
            if use_headers:
                authorization = 'Basic {:s}'.format(b64encode(':'.join(auth).encode()).decode())
                init_headers.update(Authorization=authorization)
            else:
                params.update(login=auth[0], password=auth[1])
        else:
            if use_headers:
                init_headers.update(Authorization='user_token {:s}'.format(auth))
            else:
                params.update(user_token=auth)

        response = self.session.get(url=self._set_method('initSession'),
                                    headers=init_headers,
                                    params=params)

        return {
            200: lambda r: r.json()['session_token'],
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def kill_session(self):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#kill-session>`__

        Destroy a session identified by a session token. Note that this
        method is automatically called by the context manager ``connect``.

        .. code::

            >>> glpi.kill_session()
            # Doing another actions will raise this error.
            >>> glpi.list_search_options('Computer')
            ...
            GLPIError: (ERROR_SESSION_TOKEN_INVALID) session_token semble incorrect
        """
        response = self.session.get(self._set_method('killSession'))
        {
            200: lambda r: r.text,
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def get_my_profiles(self):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#get-my-profiles>`__

        Return all the profiles associated to logged user.

        .. code::

            >>> glpi.get_my_profiles()
            [{'id': 2,
              'name': 'Observer',
              'entities': [{'id': 0, 'name': 'Root entity', 'is_recursive': 1}]},
             {'id': 8,
              'name': 'Read-Only',
              'entities': [{'id': 0, 'name': 'Root entity', 'is_recursive': 1}]}]
        """
        response = self.session.get(self._set_method('getMyProfiles'))
        return {
            200: lambda r: r.json()['myprofiles'],
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def get_active_profile(self):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#get-active-profile>`__

        Return the current active profile.

        .. code::

            >>> glpi.get_active_profile()
            {'id': 2,
             'name': 'Observer',
             'interface': 'central',
             'is_default': 0,
             ...
        """
        response = self.session.get(self._set_method('getActiveProfile'))
        return {
            200: lambda r: r.json()['active_profile'],
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def set_active_profile(self, profile_id):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#change-active-profile>`__

        Change active profile to the ``profile_id`` one.

        .. code::

            >>> glpi.get_active_profile()['name']
            'Observer'
            >>> glpi.set_active_profile(8)
            >>> glpi.get_active_profile()['name']
            'Read-Only'
            >>> glpi.set_active_profile(4) # Invalid profile for user
            GLPIError: (ERROR_ITEM_NOT_FOUND) Élément introuvable
        """
        response = self.session.post(self._set_method('changeActiveProfile'),
                                     json={'profiles_id': profile_id})
        {
            200: lambda r: bool(response.text),
            400: _glpi_error,
            401: _glpi_error,
            404: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def get_my_entities(self):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#get-my-entities>`__

        Return all the possible entities of the current logged user (and for
        current active profile).

        .. code::

            >>> glpi.get_my_entities()
            [{'id': 0, 'name': 'Root entity'}]
        """
        response = self.session.get(self._set_method('getMyEntities'))
        return {
            200: lambda r: r.json()['myentities'],
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def get_active_entities(self):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#get-active-entities>`_

        Return active entities of current logged user.

        .. code::

            >>> glpi.get_active_entity()
            {'id': 0,
             'active_entity_recursive': False,
             'active_entities': [{'id': 0}, {'id': 3}, {'id': 2}, {'id': 1}]}
        """
        response = self.session.get(self._set_method('getActiveEntities'))
        return {
            200: lambda r: r.json()['active_entity'],
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def set_active_entities(self, entity_id, is_recursive=False):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#change-active-entities>`__

        Change active entity to the ``entitie_id``.

        .. code::

            >>> glpi.set_active_entity(0, is_recursive=True)
        """
        data = {'entities_id': entity_id, 'is_recursive': is_recursive}
        response = self.session.post(self._set_method('changeActiveEntities'),
                                     json=data)
        return {
            200: lambda r: bool(response.text),
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def get_full_session(self):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#get-full-session>`__

        Return the current php $_SESSION.

        .. code::

            >>> glpi.get_full_session()
            {'glpi_plugins': {'1': 'fusioninventory', '2': 'racks', '3': 'fields'},
             'valid_id': '1ak1oms81ie61vhndhgp20b12a',
             'glpi_currenttime': '2018-09-06 14:52:31',
             ...
        """
        response = self.session.get(self._set_method('getFullSession'))
        return {
            200: lambda r: r.json()['session'],
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def get_config(self):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#get-glpi-config>`__

        Return the current $CFG_GLPI.

        .. code::

            >>> glpi.get_config()
            {'cfg_glpi': {'languages': {'ar_SA': ['العَرَبِيَّةُ',
                'ar_SA.mo',
                'ar',
            ...
        """
        response = self.session.get(self._set_method('getGlpiConfig'))
        return {
            200: lambda r: r.json(),
            400: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def get_item(self, itemtype, item_id, **kwargs):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#get-an-item)>`__

        Return the instance fields of ``itemtype`` identified by ``item_id``.
        ``kwargs`` contains additional parameters allowed by the API.

        .. code::

            >>> glpi.get_item('Computer', 1)
            {'id': 1,
             'entities_id': 0,
             'name': 'test',
             ...
            # Using with_logs extra request parameters.
            >>> glpi.get_item('Computer', 1, with_logs=True)
            {'id': 1,
             'entities_id': 0,
             'name': 'test',
             ...,
             '_logs': {
               '261': {
                 'id': 261,
                  'itemtype': 'Computer',
                  'items_id': 1,
                  ...
        """
        response = self.session.get(self._set_method(itemtype, item_id),
                                    params=_convert_bools(kwargs))
        return {
            200: lambda r: r.json(),
            400: _glpi_error,
            401: _glpi_error,
            # If object is not found, return None.
            404: lambda r: None
        }.get(response.status_code, _unknown_error)(response)

    def _add_searchtext(self, searchText):
        '''
        Generate searchText parameter.
        '''
        if not isinstance(searchText, dict):
            raise GLPIError(
                'search text should be a dict, found: {:s}'.format(str(type(searchText)))
            )

        return {'searchText[{:s}]'.format(k): v for k, v in searchText.items()}

    @_catch_errors
    def get_all_items(self, itemtype, **kwargs):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#get-all-items>`__

        Return a collection of rows of the ``itemtype``. ``kwargs`` contains
        additional parameters allowed by the API.

        .. code::

            # Retrieve (non deleted) computers.
            >>> glpi.get_all_items('Computer')
            [{'id': 1,
             'entities_id': 0,
             'name': 'test',
            ...
            # Retrieve deleted computers.
            >>> glpi.get_all_items('Computer', is_deleted=True)
            []
            # Using searchText.
            >>> glpi.get_all_items('Computer', searchText={'name':'server'})
            []
        """
        kwargs.update(self._add_searchtext(kwargs.pop('searchText', {})))
        response = self.session.get(self._set_method(itemtype),
                                    params=_convert_bools(kwargs))
        return {
            200: lambda r: r.json(),
            206: lambda r: r.json(),
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def get_sub_items(self, itemtype, item_id, sub_itemtype, **kwargs):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#get-sub-items>`__

        Return a collection of rows of the ``sub_itemtype`` for the identified
        item of type ``itemtype`` and id ``item_id``. ``kwargs`` contains
        additional parameters allowed by the API.

        .. code::

            # Retrieve logs of a computer.
            >>> In [241]: glpi.get_sub_items('Computer', 1, 'Log')
            [{'id': 261,
              'itemtype': 'Computer',
              'items_id': 1,
            ...
        """
        url = self._set_method(itemtype, item_id, sub_itemtype)
        response = self.session.get(url,
                                    params=_convert_bools(kwargs))
        return {
            200: lambda r: r.json(),
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def get_multiple_items(self, *items, **kwargs):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#get-multiple-items>`__

        Virtually call Get an item for each line in input. So, you can have a
        ticket, a user in the same query.

        .. code::

            >>> glpi.get_multiple_items({'itemtype': 'User', 'items_id': 2},
                                        {'itemtype': 'Computer', 'items_id': 1})
            [{'id': 2,
              'name': 'glpi',
              ...},
             {'id': 1,
              'entities_id': 0,
              'name': 'test',
               ...}]
        """
        def format_items(items):
            return {'items[{:d}][{:s}]'.format(idx, key): value
                    for idx, item in enumerate(items)
                    for key, value in item.items()}

        params = _convert_bools(kwargs)
        params.update(format_items(items))
        response = self.session.get(self._set_method('getMultipleItems'),
                                    params=params)
        return {
            200: lambda r: r.json(),
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def list_search_options(self, itemtype, raw=False):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#list-searchoptions>`__

        List the searchoptions of provided ``itemtype``. ``raw`` return searchoption
        uncleaned (as provided by core).

        .. code::

            >>> glpi.list_search_options('Computer')
            {'common': {'name': 'Caractéristiques'},
             '1': {
              'name': 'Nom',
              'table': 'glpi_computers',
              'field': 'name',
              'datatype': 'itemlink',
              ...
        """
        response = self.session.get(self._set_method('listSearchOptions', itemtype),
                                    params='raw' if raw else None)
        return {
            200: lambda r: r.json(),
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    def _map_fields(self, itemtype):
        """Private method that returns a mapping between fields uid and fields
        id."""
        return {re.sub('^{:s}.'.format(itemtype), '', field['uid']): field_id
                for field_id, field in self.list_search_options(itemtype).items()
                if 'uid' in field}

    def field_id(self, itemtype, field_uid, refresh=False):
        """Return ``itemtype`` field id from ``field_uid``. Each ``itemtype``
        are "cached" (in *_fields* attribute) and will be retrieve once except
        if ``refresh`` is set.

        .. code::

            >>> glpi.field_id('Computer', 'Entity.completename')
            80
        """
        # If this is already an id, just return it
        if re.match(r'^\d+$', str(field_uid)):
            return str(field_uid)

        # Retrieve and cache fields for itemtype.
        if itemtype not in self._fields or refresh:
            self._fields[itemtype] = self._map_fields(itemtype)

        return str(self._fields[itemtype][str(field_uid)])

    def field_uid(self, itemtype, field_id, refresh=False):
        """Return ``itemtype`` field uid from ``field_id``. Each ``itemtype``
        are "cached" (in *_fields* attribute) and will be retrieve once except
        if ``refresh`` is set.

        .. code::

            >>> glpi.field_id('Computer', 80)
            'Entity.completename'
        """
        # Retrieve and store fields for itemtype.
        if itemtype not in self._fields or refresh:
            self._fields[itemtype] = self._map_fields(itemtype)
        # Reverse mapping and return field uid.
        return {value: key
                for key, value in self._fields[itemtype].items()
               }[str(field_id)]

    def _add_forcedisplay(self, itemtype, value):
        return {
            'forcedisplay[{:d}]'.format(idx): self.field_id(itemtype, field)
            for idx, field in enumerate(value)
        }

    def _add_criteria(self, criteria, itemtype, parent=None):
        '''
        Recursively generate criteria/metacriteria parameters.
        '''
        if not any(isinstance(criteria, t) for t in (list, tuple, set)):
            raise GLPIError(
                'search criteria should be a list, found: {:s}'.format(str(type(criteria)))
            )

        params = {}
        for idx, criterion in enumerate(criteria):
            criterion_key = (
                'criteria[{:d}]'.format(idx)
                if parent is None
                else parent + '[criteria][{:d}]'.format(idx)
            )

            params.update(
                self._add_criteria(
                    criterion.get('criteria', []),
                    itemtype,
                    parent=criterion_key
                )
            )

            # Add parameters
            params.update(
                {
                    '{:s}[{:s}]'.format(criterion_key, p): (
                        # for 'field' key, map field id
                        self.field_id(itemtype, v)
                        if p == 'field'
                        else (v.replace("'", "''") if isinstance(v, str) else v)
                    )
                    for p, v in criterion.items()
                    if p != 'criteria'
                }
            )

        return params

    @_catch_errors
    def search(self, itemtype, **kwargs):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#search-items>`__

        Expose the GLPI searchEngine and combine criteria to retrieve a list of
        elements of specified ``itemtype``.

        .. code::

            # Retrieve
            >>> criteria = [{'field': 45, 'searchtype': 'contains', 'value': '^Ubuntu$'}]
            >>> forcedisplay = [1, 80, 45, 46] # name, entity, os name, os version
            >>> glpi.search('Computer', criteria=criteria, forcedisplay=forcedisplay)
            [{'1': 'test', '80': 'Root entity', '45': 'Ubuntu', '46': 16.04}]

            # You can use fields uid instead of fields id.
            >>> criteria = [{'field': 'Item_OperatingSystem.OperatingSystem.name',
                             'searchtype': 'contains',
                             'value': '^Ubuntu$'}]
            >>> forcedisplay = [
                    'name',
                    'Entity.completename',
                    'Item_OperatingSystem.OperatingSystem.name',
                    'Item_OperatingSystem.OperatingSystemVersion.name']
            >>> glpi.search('Computer', criteria=criteria, forcedisplay=forcedisplay)
            [{'1': 'test', '80': 'Root entity', '45': 'Ubuntu', '46': 16.04}]
        """
        params = {}
        # Format forcedisplay parameter
        params.update(self._add_forcedisplay(itemtype, kwargs.pop('forcedisplay', [])))
        # Add criteria and metacriteria
        criteria = kwargs.pop('criteria', [])
        for criterion in kwargs.pop('metacriteria', []):
            criterion['meta'] = True
            criteria.append(criterion)
        params.update(self._add_criteria(criteria, itemtype))
        # Add other parameters
        params.update(kwargs)

        response = self.session.get(self._set_method('search', itemtype), params=params)
        return {
            200: lambda r: r.json().get('data', []),
            206: lambda r: r.json().get('data', []),
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def add(self, itemtype, *items):
        """`API documentation <https://github.com
        /glpi-project/glpi/blob/master/apirest.md#add-items>`__

        Add an object (or multiple objects) of type ``itemtype`` into GLPI.

        .. code::

            >>> glpi.add('Computer',
                         {'name': 'computer1', 'serial': '123456', 'entities_id': 0},
                         {'name': 'computer2', 'serial': '234567', 'entities_id': 1})
            [{'id': 5, 'message': ''}, {'id': 6, 'message': ''}]
        """
        response = self.session.post(self._set_method(itemtype),
                                     json={'input': items})
        return {
            201: lambda r: r.json(),
            207: lambda r: r.json()[1],
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def add_sub_items(self, itemtype, item_id, sub_itemtype, *items):
        """`API documentation
        Same method used as get-sub-items, same parameter
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#get-sub-items>`__

        Add a collection of rows of the ``sub_itemtype`` for the identified
        item of type ``itemtype`` and id ``item_id``. ``kwargs`` contains
        additional parameters allowed by the API.

        .. code::

            # Add a operatingsystem of a computer.
            >>> In [241]: glpi.add_sub_items('Computer',1,'Item_OperatingSystem',{'items_id': 1 ,'itemtype':'Computer','operatingsystems_id':1 })
            [{'id': 261,
              'itemtype': 'Computer',
              'items_id': 1,
            ...
        """
        url = self._set_method(itemtype, item_id, sub_itemtype)
        response = self.session.post(url,
                                    json={'input': items})
        return {
            201: lambda r: r.json(),
            207: lambda r: r.json()[1],
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def update(self, itemtype, *items):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#update-items>`__

        Update an object (or multiple objects) existing in GLPI.

        .. code::

            >>> glpi.update('Computer',
                            {'id': 5, 'otherserial': 'abcdef'})
            >>> glpi.update('Computer',
                            {'id': 5, 'otherserial': 'abcdef'},
                            {'id': 6, 'otherserial': 'bcdefg'})
            [{'5': True, 'message': ''}, {'6': True, 'message': ''}]
        """
        response = self.session.put(self._set_method(itemtype),
                                    json={'input': items})
        return {
            200: lambda r: r.json(),
            201: lambda r: r.json(),
            207: lambda r: r.json()[1],
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def update_sub_items(self, itemtype, item_id, sub_itemtype, *items):
        """`API documentation
        Same method used as get-sub-items, same parameters
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#get-sub-items>`__

        Updates a collection of rows of the ``sub_itemtype`` for the identified
        item of type ``itemtype`` and id ``item_id``. ``kwargs`` contains
        additional parameters allowed by the API.

        .. code::

            # update the operatingsystem a computer.
            >>> In [241]: glpi.update_sub_items('Computer',1,'Item_OperatingSystem' {'id': 1 ,'itemtype':'Computer','operatingsystem_id':1 })
            [{'id': 261,
              'itemtype': 'Computer',
              'items_id': 1,
            ...
        """
        url = self._set_method(itemtype, item_id, sub_itemtype)
        response = self.session.put(url,
                                    json={'input': items})
        return {
            200: lambda r: r.json(),
            201: lambda r: r.json(),
            207: lambda r: r.json(),
            400: _glpi_error,
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def delete(self, itemtype, *items, **kwargs):
        """`API documentation <https://github.com
        /glpi-project/glpi/blob/master/apirest.md#delete-items>`__

        Delete an object existing in GLPI.

        .. code::

            # Move some computers to the trash.
            >>> glpi.delete('Computer', {'id': 5}, {'id': 6})
            [{'5': True, 'message': ''}, {'6': True, 'message': ''}]
            # Purge computers.
            >>> glpi.delete('Computer', {'id': 2}, {'id': 5}, force_purge=True)
            [{'2': True, 'message': ''}, {'5': True, 'message': ''}]
            # With non existing items
            >>> glpi.delete('Computer', {'id': 2}, {'id': 101}, force_purge=True)
            [{'2': True, 'message': ''}, {'101': False, 'message': 'Item not found'}]
        """
        response = self.session.delete(self._set_method(itemtype),
                                       params=_convert_bools(kwargs),
                                       json={'input': items})
        return {
            200: lambda r: r.json(),
            204: lambda r: r.json(),
            207: lambda r: r.json()[1],
            400: lambda r: _glpi_error(r) if r.json()[0] != 'ERROR_GLPI_DELETE' else r.json()[1],
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)

    @_catch_errors
    def delete_sub_items(self, itemtype, item_id, sub_itemtype, *items):
        """`API documentation
        Same method used as get-sub-items, same parameters
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#get-sub-items>`__

        deletes a collection of rows of the ``sub_itemtype`` for the identified
        item of type ``itemtype`` and id ``item_id``. ``kwargs`` contains
        additional parameters allowed by the API.

        .. code::

            # delete the operatingsystem a computer.
            >>> In [241]: glpi.delete_sub_items('Computer',1,'Item_OperatingSystem' {'id': 1 ,'itemtype':'Computer','operatingsystem_id':1 })
            [{'id': 261,
              'itemtype': 'Computer',
              'items_id': 1,
            ...
        """
        url = self._set_method(itemtype, item_id, sub_itemtype)
        response = self.session.delete(url,
                                    json={'input': items})
        return {
            200: lambda r: r.json(),
            204: lambda r: r.json(),
            207: lambda r: r.json()[1],
            400: lambda r: _glpi_error(r) if r.json()[0] != 'ERROR_GLPI_DELETE' else r.json()[1],
            401: _glpi_error
        }.get(response.status_code, _unknown_error)(response)


    @_catch_errors
    def upload_document(self, name, filepath):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#upload-a-document-file>`__

        Upload the file at ``filepath`` as a document named ``name``.

        .. code::

            glpi.upload_document("My test document", '/path/to/file/locally')
            {'id': 55,
             'message': 'Item successfully added: My test document',
             'upload_result': {'filename': [{'name': ...}]}}

        There may be errors while uploading the file (like a non managed file type).
        In this case, the API create a document but without a file attached to it.
        This method raise a warning (and another warning if the document could not
        be deleted for some reasons) and purge the created but incomplete document.
        """
        with open(filepath, 'rb') as fhandler:
            response = self.session.post(
                url=self._set_method('Document'),
                headers={'Content-Type': None},
                files={
                    'uploadManifest': (
                        None,
                        _UPLOAD_MANIFEST.format(name=name, filename=os.path.basename(filepath)),
                        'application/json'
                    ),
                    'filename[0]': (filepath, fhandler)
                }
            )

        if response.status_code != 201:
            _glpi_error(response)

        doc_id = response.json()['id']
        error = response.json()['upload_result']['filename'][0].get('error', None)
        if error is not None:
            warnings.warn(_WARN_DEL_DOC.format(doc_id), UserWarning)
            try:
                self.delete('Document', {'id': doc_id}, force_purge=True)
            except GLPIError as err:
                warnings.warn(_WARN_DEL_ERR.format(doc_id, str(err)), UserWarning)
            raise GLPIError('(ERROR_GLPI_INVALID_DOCUMENT) {:s}'.format(error))

        return response.json()

    @_catch_errors
    def download_document(self, doc_id, dirpath, filename=None):
        """`API documentation
        <https://github.com/glpi-project/glpi/blob/master/apirest.md#download-a-document-file>`__

        Download the file of the document with id ``doc_id`` in the directory
        ``dirpath``. If ``filename`` is not set, the name of the file is retrieved
        from the server otherwise the given value is used. The local path of the file
        is returned by the method.

        .. code::

            glpi.download_file(1, '/tmp')
            /tmp/test.txt
            glpi.download_file(1, '/tmp', filename='thenameiwant.txt')
            /tmp/thenameiwant.txt
        """
        if not os.path.exists(dirpath):
            raise GLPIError("unable to download file of document '{:d}': directory "
                            "'{:s}' does not exists".format(doc_id, dirpath))

        response = self.session.get(
            url=self._set_method('Document', doc_id),
            headers={
                'Session-Token': self.session.headers['Session-Token'],
                'App-Token': self.session.headers['App-Token'],
                'Accept': 'application/octet-stream'
            }
        )
        if response.status_code != 200:
            _glpi_error(response)

        filename = filename or _FILENAME_RE.findall(response.headers['Content-disposition'])[0]
        filepath = os.path.join(dirpath, filename)
        with open(filepath, 'wb') as fhandler:
            fhandler.write(response.content)
        return filepath
