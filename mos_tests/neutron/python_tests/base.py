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

from mos_tests import settings


class NotFound(Exception):
    message = "Not Found."


class TestBase(object):

    @pytest.fixture(autouse=True)
    def init(self, fuel, env, os_conn):
        self.fuel = fuel
        self.env = env
        self.os_conn = os_conn

    def get_node_with_dhcp(self, net_id):
        nodes = self.os_conn.get_node_with_dhcp_for_network(net_id)
        if not nodes:
            raise NotFound("Nodes with dhcp for network with id:{}"
                           " not found.".format(net_id))

        return self.env.find_node_by_fqdn(nodes[0])

    def run_on_vm(self, vm, vm_keypair, command, vm_login="cirros"):
        command = command.replace('"', r'\"')
        net_name = [x for x in vm.addresses if len(vm.addresses[x]) > 0][0]
        vm_ip = vm.addresses[net_name][0]['addr']
        net_id = self.os_conn.neutron.list_networks(
            name=net_name)['networks'][0]['id']
        dhcp_namespace = "qdhcp-{0}".format(net_id)
        devops_node = self.get_node_with_dhcp(net_id)
        _ip = devops_node.data['ip']
        with self.env.get_ssh_to_node(_ip) as remote:
            res = remote.execute(
                'ip netns list | grep -q {0}'.format(dhcp_namespace)
            )
            if res['exit_code'] != 0:
                raise Exception("Network namespace '{0}' doesn't exist on "
                                "remote slave!".format(dhcp_namespace))
            key_path = '/tmp/instancekey_rsa'
            res = remote.execute(
                'echo "{0}" > {1} ''&& chmod 400 {1}'.format(
                    vm_keypair.private_key, key_path))
            cmd = (
                ". openrc; ip netns exec {ns} ssh -i {key_path}"
                " -o 'StrictHostKeyChecking no'"
                " {vm_login}@{vm_ip} \"{command}\""
            ).format(
                ns=dhcp_namespace,
                key_path=key_path,
                vm_login=vm_login,
                vm_ip=vm_ip,
                command=command)
            err_msg = ("SSH command:\n{command}\nwas not completed with "
                       "exit code 0 after 3 attempts with 1 minute timeout.")
            results = []

            def run(cmd):
                results.append(remote.execute(cmd))
                return results[-1]

            wait(lambda: run(cmd)['exit_code'] == 0,
                 interval=60, timeout=3 * 60,
                 timeout_msg=err_msg.format(command=cmd))
            return results[-1]

    def check_ping_from_vm(self, vm, vm_keypair, ip_to_ping=None):
        if ip_to_ping is None:
            ip_to_ping = settings.PUBLIC_TEST_IP
        cmd = "ping -c1 {ip}".format(ip=ip_to_ping)
        res = self.run_on_vm(vm, vm_keypair, cmd)
        assert (0 == res['exit_code'],
                     'Instance has no connectivity, exit code {0},'
                     'stdout {1}, stderr {2}'.format(res['exit_code'],
                                                     res['stdout'],
                                                     res['stderr'])
        )

    def check_vm_connectivity(self):
        """Check that all vms can ping each other and public ip"""
        servers = self.os_conn.get_servers()
        for server1 in servers:
            for server2 in servers:
                if server1 == server2:
                    continue
                for ip in (
                    self.os_conn.get_nova_instance_ips(server2).values() +
                    [settings.PUBLIC_TEST_IP]
                ):
                    self.check_ping_from_vm(server1, self.instance_keypair, ip)
