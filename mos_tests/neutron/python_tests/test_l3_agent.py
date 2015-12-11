#    Copyright 2015 Mirantis, Inc.
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

from devops.helpers.helpers import wait

from tools.settings import logger
from mos_tests.neutron.python_tests.base import TestBase


@pytest.mark.usefixtures("check_ha_env", "check_several_computes", "setup")
class TestL3Agent(TestBase):

    def ban_l3_agent(self, _ip, router_name, wait_for_migrate=True,
                     wait_for_die=True):
        """Ban L3 agent and wait until router rescheduling

        Ban L3 agent on same node as router placed and wait until router
        rescheduling

        :param _ip: ip of server to to execute ban command
        :param router_name: name of router to determine node with L3 agent
        :param wait_for_migrate: wait until router migrate to new controller
        :param wait_for_die: wait for l3 agent died
        :returns: str -- name of banned node
        """
        router = self.os_conn.neutron.list_routers(
            name=router_name)['routers'][0]
        node_with_l3 = self.os_conn.get_l3_agent_hosts(router['id'])[0]

        # ban l3 agent on this node
        with self.env.get_ssh_to_node(_ip) as remote:
            remote.execute(
                "pcs resource ban p_neutron-l3-agent {0}".format(node_with_l3))

        logger.info("Ban L3 agent on node {0}".format(node_with_l3))

        # wait for l3 agent died
        if wait_for_die:
            wait(
                lambda: self.os_conn.get_l3_for_router(
                    router['id'])['agents'][0]['alive'] is False,
                timeout=60 * 3, timeout_msg="L3 agent is alive"
            )

        # Wait to migrate l3 agent on new controller
        if wait_for_migrate:
            err_msg = "l3 agent wasn't banned, it is still {0}"
            wait(lambda: not node_with_l3 == self.os_conn.get_l3_agent_hosts(
                 router['id'])[0], timeout=60 * 3,
                 timeout_msg=err_msg.format(node_with_l3))
        return node_with_l3

    def clear_l3_agent(self, _ip, router_name, node, wait_for_alive=False):
        """Clear L3 agent ban and wait until router moved to this node

        Clear previously banned L3 agent on node wait until ruter moved to this
        node

        :param _ip: ip of server to to execute clear command
        :param router_name: name of router to wait until it move to node
        :param node: name of node to clear
        """
        router = self.os_conn.neutron.list_routers(
            name=router_name)['routers'][0]
        with self.env.get_ssh_to_node(_ip) as remote:
            remote.execute(
                "pcs resource clear p_neutron-l3-agent {0}".format(node))

        logger.info("Clear L3 agent on node {0}".format(node))

        # wait for l3 agent alive
        if wait_for_alive:
            wait(
                lambda: self.os_conn.get_l3_for_router(
                    router['id'])['agents'][0]['alive'] is True,
                timeout=60 * 3, timeout_msg="L3 agent is dead yet"
            )

    @pytest.fixture(autouse=True)
    def prepare_openstack(self, init):
        """Prepare OpenStack for scenarios run

        Steps:
            1. Create network1, network2
            2. Create router1 and connect it with network1, network2 and
                external net
            3. Boot vm1 in network1 and associate floating ip
            4. Boot vm2 in network2
            5. Add rules for ping
            6. Ping 8.8.8.8, vm1 (both ip) and vm2 (fixed ip) from each other
        """
        # init variables
        exist_networks = self.os_conn.list_networks()['networks']
        ext_network = [x for x in exist_networks
                       if x.get('router:external')][0]
        self.zone = self.os_conn.nova.availability_zones.find(zoneName="nova")
        self.security_group = self.os_conn.create_sec_group_for_ssh()
        self.hosts = self.zone.hosts.keys()[:2]
        self.instance_keypair = self.os_conn.create_key(key_name='instancekey')

        # create router
        router = self.os_conn.create_router(name="router01")
        self.os_conn.router_gateway_add(router_id=router['router']['id'],
                                        network_id=ext_network['id'])

        # create 2 networks and 2 instances
        for i, hostname in enumerate(self.hosts, 1):
            network = self.os_conn.create_network(name='net%02d' % i)
            subnet = self.os_conn.create_subnet(
                network_id=network['network']['id'],
                name='net%02d__subnet' % i,
                cidr="192.168.%d.0/24" % i)
            self.os_conn.router_interface_add(
                router_id=router['router']['id'],
                subnet_id=subnet['subnet']['id'])
            self.os_conn.create_server(
                name='server%02d' % i,
                availability_zone='{}:{}'.format(self.zone.zoneName, hostname),
                key_name=self.instance_keypair.name,
                nics=[{'net-id': network['network']['id']}],
                security_groups=[self.security_group.id])

        # add floating ip to first server
        server1 = self.os_conn.nova.servers.find(name="server01")
        self.os_conn.assign_floating_ip(server1)

        # check pings
        self.check_vm_connectivity()

    @pytest.mark.parametrize('ban_count', [1, 2], ids=['single', 'twice'])
    def test_ban_one_l3_agent(self, ban_count):
        """Check l3-agent rescheduling after l3-agent dies on vlan

        Scenario:
            1. Revert snapshot with neutron cluster
            2. Create network1, network2
            3. Create router1 and connect it with network1, network2 and
               external net
            4. Boot vm1 in network1 and associate floating ip
            5. Boot vm2 in network2
            6. Add rules for ping
            7. ping 8.8.8.8, vm1 (both ip) and vm2 (fixed ip) from each other
            8. get node with l3 agent on what is router1
            9. ban this l3 agent on the node with pcs
                (e.g. pcs resource ban p_neutron-l3-agent
                node-3.test.domain.local)
            10. wait some time (about 20-30) while pcs resource and
                neutron agent-list will show that it is dead
            11. Check that router1 was rescheduled
            12. Boot vm3 in network1
            13. ping 8.8.8.8, vm1 (both ip), vm2 (fixed ip) and vm3 (fixed ip)
                from each other

        Duration 30m

        """
        net_id = self.os_conn.neutron.list_networks(
            name="net01")['networks'][0]['id']
        devops_node = self.get_node_with_dhcp(net_id)
        ip = devops_node.data['ip']

        # ban l3 agent
        for _ in range(ban_count):
            self.ban_l3_agent(_ip=ip, router_name="router01")

        # create another server on net01
        net01 = self.os_conn.nova.networks.find(label="net01")
        self.os_conn.create_server(
            name='server03',
            availability_zone='{}:{}'.format(self.zone.zoneName,
                                             self.hosts[0]),
            key_name=self.instance_keypair.name,
            nics=[{'net-id': net01.id}],
            security_groups=[self.security_group.id])

        # check pings
        self.check_vm_connectivity()

    def test_ban_l3_agents_and_clear_last(self):
        """Ban all l3-agents, clear last of them and check health of l3-agent

        Scenario:
            1. Revert snapshot with neutron cluster
            2. Create network1, network2
            3. Create router1 and connect it with network1, network2 and
               external net
            4. Boot vm1 in network1 and associate floating ip
            5. Boot vm2 in network2
            6. Add rules for ping
            7. ping 8.8.8.8, vm1 (both ip) and vm2 (fixed ip) from each other
            8. Ban l3-agent on what router1 is
            9. Wait for route rescheduling
            10. Repeat steps 7-8 twice
            11. Clear last L3 agent
            12. Check that router moved to the health l3-agent
            13. Boot one more VM (VM3) in network1
            14. Boot vm3 in network1
            15. ping 8.8.8.8, vm1 (both ip), vm2 (fixed ip) and vm3 (fixed ip)
                from each other

        Duration 30m

        """
        net_id = self.os_conn.neutron.list_networks(
            name="net01")['networks'][0]['id']
        devops_node = self.get_node_with_dhcp(net_id)
        ip = devops_node.data['ip']

        # ban l3 agents
        for _ in range(2):
            self.ban_l3_agent(router_name="router01", _ip=ip)
        last_banned_node = self.ban_l3_agent(router_name="router01",
                                             _ip=ip,
                                             wait_for_migrate=False)

        # clear last banned l3 agent
        self.clear_l3_agent(_ip=ip,
                            router_name="router01",
                            node=last_banned_node,
                            wait_for_alive=True)

        # create another server on net01
        net01 = self.os_conn.nova.networks.find(label="net01")
        self.os_conn.create_server(
            name='server03',
            availability_zone='{}:{}'.format(self.zone.zoneName,
                                             self.hosts[0]),
            key_name=self.instance_keypair.name,
            nics=[{'net-id': net01.id}],
            security_groups=[self.security_group.id])

        # check pings
        self.check_vm_connectivity()
