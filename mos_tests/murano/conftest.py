#    Copyright 2016 Mirantis, Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import pytest

from mos_tests.functions import os_cli


@pytest.yield_fixture
def controller_remote(env):
    with env.get_nodes_by_role('controller')[0].ssh() as remote:
        yield remote


@pytest.fixture
def openstack_client(controller_remote):
    return os_cli.OpenStack(controller_remote)


@pytest.yield_fixture
def environment(os_conn):
    environment = os_conn.murano.environments.create(
        {'name': os_conn.rand_name('MuranoEnv')})
    yield environment
    os_conn.murano.environments.delete(environment.id)


@pytest.yield_fixture
def session(os_conn, environment):
    session = os_conn.murano.sessions.configure(environment.id)
    yield session
    os_conn.murano.sessions.delete(environment.id, session.id)


@pytest.yield_fixture
def keypair(os_conn):
    keypair = os_conn.create_key(key_name='murano-key')
    yield keypair
    os_conn.delete_key(key_name=keypair.name)
