# pakreq.py

import logging

from datetime import datetime
from packaging import version

from pakreq.db import OAuthInfo
from pakreq.db import RequestStatus, RequestType, REQUEST, USER
from pakreq.db import get_max_id, get_row, get_rows, update_row, init_db
from pakreq.packages import get_package_info, search_packages

logger = logging.getLogger(__name__)


async def find_package(name):
    info = get_package_info(name)
    if info['pkg']:
        return info['pkg']['name']
    info = search_packages(name)
    name_stripped = name.replace('-', '')
    for package in info['packages']:
        if package['name'] == name or package['name'] == name_stripped:
            return package['name']
    return None


async def new_request(
    conn, id=None, status=RequestStatus.OPEN, rtype=RequestType.PAKREQ,
    name='Unknown', description='Unknown',
    requester_id=0, packager_id=0,
    date=datetime.now(), note=None
):
    """Create new request"""
    # Initializing values
    id = id or (await get_max_id(conn, REQUEST) + 1)
    statement = REQUEST.insert(None).values(
        id=id, status=status, type=rtype, name=name,
        description=description, requester_id=requester_id,
        packager_id=packager_id, pub_date=date,
        note=note
    )
    await conn.execute(statement)
    await conn.commit()


async def get_request_detail(conn, id):
    """Not just fetch request info, but also user info"""
    result = await get_row(conn, REQUEST, id)
    # Get requester & packager information
    try:
        result['requester'] = await get_row(conn, USER, result['requester_id'])
    except Exception:
        result['requester'] = dict(id='0', username='Unknown')
    try:
        result['packager'] = await get_row(conn, USER, result['packager_id'])
    except Exception:
        result['packager'] = dict(id='0', username='Unknown')
    return result


async def new_user(
    conn, username, id=None, admin=False,
    password_hash=None, oauth_info=OAuthInfo()
):
    """Create new user"""
    # Initializing values
    id = id or (await get_max_id(conn, USER) + 1)
    statement = USER.insert(None).values(
        id=id, username=username, admin=admin,
        password_hash=password_hash,
        oauth_info=oauth_info.output()
    )
    await conn.execute(statement)
    await conn.commit()


async def get_users(conn):
    """List all the users (wrapper of get_rows)"""
    return await get_rows(conn, USER)


async def get_requests(conn):
    """List all the requests (wrapper of get_rows)"""
    return await get_rows(conn, REQUEST)


async def get_max_user_id(conn):
    """Fetch max user id (wrapper of get_max_id)"""
    return await get_max_id(conn, USER)


async def get_max_request_id(conn):
    """Fetch max request id (wrapper of get_max_id)"""
    return await get_max_id(conn, REQUEST)


async def get_user(conn, id):
    """Get user info by ID (wrapper of get_row)"""
    return await get_row(conn, USER, id)


async def get_request(conn, id):
    """Get request info by ID (wrapper of get_row)"""
    return await get_row(conn, REQUEST, id)


async def update_user(conn, id, **kwargs):
    """Update user by ID (wrapper of update_row)"""
    await update_row(conn, USER, id, kwargs)


async def update_request(conn, id, **kwargs):
    """Update request by ID (wrapper of update_row)"""
    await update_row(conn, REQUEST, id, kwargs)


# Daemon part
class Demon(object):
    """Maintenance daemon"""

    def __init__(self, config):
        self.app = dict()
        self.app['config'] = config

    async def init_db(self):
        """Initialize database connection"""
        await init_db(self.app)

    async def clean(self):
        """Cleanup finished requests"""
        async with self.app['db'].acquire() as conn:
            requests = await get_requests(conn)
            open_requests = [request for request in requests if request['status'] == RequestStatus.OPEN]
            for request in open_requests:
                if request['type'] == RequestType.PAKREQ:
                    if await find_package(request['name']):
                        logger.info('%s has been packaged, closing' % request['name'])
                        await update_request(
                            conn, request['id'], status=RequestStatus.DONE,
                            note='This package has been packaged.'
                        )
                elif request['type'] == RequestType.UPDREQ:
                    if await find_package(request['name']):
                        info = get_package_info(request['name'])
                        try:
                            if version.parse(info['version']) > version.parse(request['description']):
                                await update_request(
                                    conn, request['id'], status=RequestStatus.DONE,
                                    note='Current version: %s' % info['version']
                                )
                        except Exception:
                            pass
                    else:
                        await update_request(
                            conn, request['id'],status=RequestStatus.REJECTED,
                            note='404 Package not found'
                        )

    async def daemon_start(self):
        """Maintenance daemon"""
        logger.info('Maintenance daemon starting...')
        # Need some scheduling stuff