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

import json
import logging
import random
import socket
import telnetlib
import time
import yaml

from cinderclient import client as cinderclient
from glanceclient.v2.client import Client as GlanceClient
from heatclient.v1.client import Client as HeatClient
from keystoneclient.auth.identity.v2 import Password as KeystonePassword
from keystoneclient import session
from keystoneclient.v2_0 import Client as KeystoneClient
from muranoclient.v1.client import Client as MuranoClient
from neutronclient.common.exceptions import NeutronClientException
from neutronclient.v2_0 import client as neutron_client
from novaclient import client as nova_client
from novaclient.exceptions import ClientException as NovaClientException
import paramiko
import six

from mos_tests.environment.ssh import SSHClient
from mos_tests.functions.common import gen_temp_file
from mos_tests.functions.common import wait
from mos_tests.functions import os_cli

logger = logging.getLogger(__name__)


class OpenStackActions(object):
    """OpenStack base services clients and helper actions"""

    def __init__(self, controller_ip, user='admin', password='admin',
                 tenant='admin', cert=None, env=None, proxy_session=None):
        logger.debug('Init OpenStack clients on {0}'.format(controller_ip))
        self.controller_ip = controller_ip

        self.username = user
        self.password = password
        self.tenant = tenant

        if cert is None:
            auth_url = 'http://{0}:5000/v2.0/'.format(self.controller_ip)
            self.path_to_cert = None
            self.insecure = True
        else:
            auth_url = 'https://{0}:5000/v2.0/'.format(self.controller_ip)
            with gen_temp_file(prefix="fuel_cert_", suffix=".pem") as f:
                f.write(cert)
            self.path_to_cert = f.name
            self.insecure = False

        logger.debug('Auth URL is {0}'.format(auth_url))

        auth = KeystonePassword(username=user,
                                password=password,
                                auth_url=auth_url,
                                tenant_name=tenant)

        self.session = session.Session(auth=auth, verify=self.path_to_cert)

        self.keystone = KeystoneClient(session=self.session)
        self.keystone.management_url = auth_url

        self.nova = nova_client.Client(version=2, session=self.session)

        self.cinder = cinderclient.Client(version=2, session=self.session)

        self.neutron = neutron_client.Client(session=self.session)

        self.glance = GlanceClient(session=self.session)

        endpoint_url = self.session.get_endpoint(service_type='orchestration',
                                                 endpoint_type='publicURL')
        token = self.session.get_token()
        self.heat = HeatClient(endpoint=endpoint_url, token=token)

        murano_endpoint = self.session.get_endpoint(
            service_type='application_catalog', endpoint_type='publicURL')
        self.murano = MuranoClient(endpoint=murano_endpoint, token=token,
                                   cacert=self.path_to_cert)
        self.env = env

    def _get_cirros_image(self):
        for image in self.glance.images.list():
            if image.name.startswith("TestVM"):
                return image

    def is_nova_ready(self):
        """Checks that all nova computes are available"""
        hosts = self.nova.availability_zones.find(zoneName="nova").hosts
        return all(x['available'] for y in hosts.values()
                   for x in y.values() if x['active'])

    def get_instance_detail(self, server):
        details = self.nova.servers.get(server)
        return details

    def get_servers(self):
        servers = self.nova.servers.list()
        if servers:
            return servers

    def get_srv_hypervisor_name(self, srv):
        srv = self.nova.servers.get(srv.id)
        return getattr(srv, "OS-EXT-SRV-ATTR:hypervisor_hostname")

    def is_server_active(self, server):
        status = self.nova.servers.get(server).status
        if status == 'ACTIVE':
            return True
        if status == 'ERROR':
            raise Exception('Server {} status is error'.format(server.name))

    def create_server(self, name, image_id=None, flavor=1, userdata=None,
                      files=None, key_name=None, timeout=300,
                      wait_for_active=True, wait_for_avaliable=True, **kwargs):

        if image_id is None:
            image_id = self._get_cirros_image().id
        srv = self.nova.servers.create(name=name,
                                       image=image_id,
                                       flavor=flavor,
                                       userdata=userdata,
                                       files=files,
                                       key_name=key_name,
                                       **kwargs)

        if wait_for_active:
            wait(lambda: self.is_server_active(srv),
                 timeout_seconds=timeout, sleep_seconds=5,
                 waiting_for='instance {0} changes status to ACTIVE'.format(
                    name))

        # wait for ssh ready
        if wait_for_avaliable:
            if self.env is not None:
                wait(lambda: self.is_server_ssh_ready(srv),
                     timeout_seconds=timeout,
                     waiting_for='server available via ssh')
            logger.info('the server {0} is ready'.format(srv.name))
        return self.get_instance_detail(srv.id)

    def is_server_ssh_ready(self, server):
        """Check ssh connect to server"""
        try:
            with self.ssh_to_instance(self.env, server, username='cirros',
                password='cubswin:)'
            ):
                return True
        except paramiko.SSHException as e:
            if 'authentication' in unicode(e).lower():
                return True
            else:
                logger.debug('Instance unavailable yet: {}'.format(e))
                return False
        except Exception as e:
            logger.error(e)

    def get_nova_instance_ips(self, srv):
        """Return all nova instance ip addresses as dict

        Example return:
        {'floating': '10.109.2.2',
        'fixed': '192.168.1.2'}

        :param srv: nova instance
        :rtype: dict
        :return: Dict with server ips
        """
        return {x['OS-EXT-IPS:type']: x['addr']
                for y in srv.addresses.values()
                for x in y}

    def get_node_with_dhcp_for_network(self, net_id, filter_attr='host',
                                       is_alive=True):
        filter_fn = lambda x: x[filter_attr] if filter_attr else x
        result = self.list_dhcp_agents_for_network(net_id)
        nodes = [filter_fn(node) for node in result['agents']
                 if node['alive'] == is_alive]
        return nodes

    def get_node_with_dhcp_for_network_by_host(self, net_id, hostname):
        result = self.list_dhcp_agents_for_network(net_id)
        nodes = [node for node in result['agents'] if node['host'] == hostname]
        return nodes

    def list_all_neutron_agents(self, agent_type=None,
                                filter_attr=None, is_alive=True):
        agents_type_map = {
            'dhcp': 'neutron-dhcp-agent',
            'ovs': 'neutron-openvswitch-agent',
            'metadata': 'neutron-metadata-agent',
            'l3': 'neutron-l3-agent',
            None: ''
        }
        filter_fn = lambda x: x[filter_attr] if filter_attr else x
        agents = [
            filter_fn(agent) for agent in self.neutron.list_agents(
                binary=agents_type_map[agent_type])['agents']
            if agent['alive'] == is_alive]
        return agents

    def list_dhcp_agents_for_network(self, net_id):
        return self.neutron.list_dhcp_agent_hosting_networks(net_id)

    def get_networks_on_dhcp_agent(self, agent_id):
        return self.list_networks_on_dhcp_agent(agent_id)['networks']

    def list_networks_on_dhcp_agent(self, agent_id):
        return self.neutron.list_networks_on_dhcp_agent(agent_id)

    def add_network_to_dhcp_agent(self, agent_id, network_id):
        self.neutron.add_network_to_dhcp_agent(
            agent_id, body={'network_id': network_id})

    def remove_network_from_dhcp_agent(self, agent_id, network_id):
        self.neutron.remove_network_from_dhcp_agent(agent_id, network_id)

    def add_router_to_l3_agent(self, router_id, l3_agent_id):
        return self.neutron.add_router_to_l3_agent(l3_agent_id,
                                                   {'router_id': router_id})

    def remove_router_from_l3_agent(self, router_id, l3_agent_id):
        return self.neutron.remove_router_from_l3_agent(router_id=router_id,
                                                        l3_agent=l3_agent_id)

    def list_ports_for_network(self, network_id, device_owner):
        return self.neutron.list_ports(
            network_id=network_id, device_owner=device_owner)['ports']

    def list_l3_agents(self):
        return self.list_all_neutron_agents('l3')

    def get_l3_agent_hosts(self, router_id):
        result = self.get_l3_for_router(router_id)
        hosts = [i['host'] for i in result['agents']]
        return hosts

    def get_l3_for_router(self, router_id):
        return self.neutron.list_l3_agent_hosting_routers(router_id)

    def create_network(self, name, tenant_id=None):
        network = {'name': name, 'admin_state_up': True}
        if tenant_id is not None:
            network['tenant_id'] = tenant_id
        return self.neutron.create_network({'network': network})

    def delete_network(self, id):
        return self.neutron.delete_network(id)

    def create_subnet(self, network_id, name, cidr, tenant_id=None,
                      dns_nameservers=None):
        subnet = {
            "network_id": network_id,
            "ip_version": 4,
            "cidr": cidr,
            "name": name
        }
        if tenant_id is not None:
            subnet['tenant_id'] = tenant_id
        if dns_nameservers is not None:
            subnet['dns_nameservers'] = dns_nameservers
        return self.neutron.create_subnet({'subnet': subnet})

    def delete_subnet(self, id):
        return self.neutron.delete_subnet(id)

    def list_networks(self):
        return self.neutron.list_networks()

    def assign_floating_ip(self, srv, use_neutron=False):
        if use_neutron:
            #   Find external net id for tenant
            nets = self.neutron.list_networks()['networks']
            err_msg = "Active external network not found in nets:{}"
            ext_net_ids = [
                net['id'] for net in nets
                if net['router:external'] and net['status'] == "ACTIVE"]
            assert ext_net_ids, err_msg.format(nets)
            net_id = ext_net_ids[0]
            #   Find instance port
            ports = self.neutron.list_ports(device_id=srv.id)['ports']
            err_msg = "Not found active ports for instance:{}"
            assert ports, err_msg.format(srv.id)
            port = ports[0]
            #   Create floating IP
            body = {'floatingip': {'floating_network_id': net_id,
                                   'port_id': port['id']}}
            flip = self.neutron.create_floatingip(body)
            #   Wait active state for port
            port_id = flip['floatingip']['port_id']
            wait(lambda: self.neutron.show_port(port_id)['port']['status'] ==
                    "ACTIVE",
                 timeout_seconds=60,
                 waiting_for="floating_ip port is active")
            return flip['floatingip']

        fl_ips_pool = self.nova.floating_ip_pools.list()
        if fl_ips_pool:
            floating_ip = self.nova.floating_ips.create(
                pool=fl_ips_pool[0].name)
            self.nova.servers.add_floating_ip(srv, floating_ip)
            return floating_ip

    def disassociate_floating_ip(self, srv, floating_ip, use_neutron=False):
        def is_floating_ip_down():
            fl_ip = self.neutron.show_floatingip(identifier)
            return fl_ip['floatingip']['status'] == 'DOWN'
        if use_neutron:
            try:
                self.neutron.update_floatingip(
                    floatingip=floating_ip['id'],
                    body={'floatingip': {}})

                identifier = floating_ip['id']
                wait(is_floating_ip_down, timeout_seconds=60)
            except NeutronClientException:
                logger.info('The floatingip {} can not be disassociated.'
                            .format(floating_ip['id']))
        else:
            try:
                self.nova.servers.remove_floating_ip(srv, floating_ip)
            except NovaClientException:
                logger.info('The floatingip {} can not be disassociated.'
                            .format(floating_ip))

    def delete_floating_ip(self, floating_ip, use_neutron=False):
        if use_neutron:
            try:
                self.neutron.delete_floatingip(floating_ip['id'])
            except NeutronClientException:
                logger.info('floating_ip {} is not deletable'
                            .format(floating_ip['id']))
        else:
            try:
                self.nova.floating_ips.delete(floating_ip)
            except NovaClientException:
                logger.info('floating_ip {} is not deletable'
                            .format(floating_ip))

    def create_router(self, name, tenant_id=None, distributed=False):
        router = {'name': name, 'distributed': distributed}
        if tenant_id is not None:
            router['tenant_id'] = tenant_id
        return self.neutron.create_router({'router': router})

    def router_interface_add(self, router_id, subnet_id):
        subnet = {
            'subnet_id': subnet_id
        }
        self.neutron.add_interface_router(router_id, subnet)

    def router_gateway_add(self, router_id, network_id):
        network = {
            'network_id': network_id
        }
        self.neutron.add_gateway_router(router_id, network)

    def create_sec_group_for_ssh(self):
        name = "test-sg" + str(random.randint(1, 0x7fffffff))
        secgroup = self.nova.security_groups.create(
            name, "descr")

        rulesets = [
            {
                # ssh
                'ip_protocol': 'tcp',
                'from_port': 22,
                'to_port': 22,
                'cidr': '0.0.0.0/0',
            },
            {
                # ping
                'ip_protocol': 'icmp',
                'from_port': -1,
                'to_port': -1,
                'cidr': '0.0.0.0/0',
            }
        ]

        for ruleset in rulesets:
            self.nova.security_group_rules.create(
                secgroup.id, **ruleset)
        return secgroup

    def create_key(self, key_name):
        return self.nova.keypairs.create(key_name)

    def delete_key(self, key_name):
        return self.nova.keypairs.delete(key_name)

    def get_port_by_fixed_ip(self, ip):
        """Returns neutron port by instance fixed ip"""
        for port in self.neutron.list_ports()['ports']:
            for ips in port['fixed_ips']:
                if ip == ips['ip_address']:
                    return port

    @property
    def ext_network(self):
        exist_networks = self.list_networks()['networks']
        return [x for x in exist_networks if x.get('router:external')][0]

    def delete_subnets(self, networks):
        # Subnets and ports are simply filtered by network ids
        for subnet in self.neutron.list_subnets()['subnets']:
            if subnet['network_id'] not in networks:
                continue
            try:
                self.neutron.delete_subnet(subnet['id'])
            except NeutronClientException:
                logger.info(
                    'the subnet {} is not deletable'.format(subnet['id']))

    def delete_routers(self):
        # Did not find the better way to detect the fuel admin router
        # Looks like it just always has fixed name router04
        for router in self.neutron.list_routers()['routers']:
            if router['name'] == 'router04':
                continue
            try:
                self.neutron.delete_router(router['id'])
            except NeutronClientException:
                logger.info('the router {} is not deletable'.format(router))

    def delete_floating_ips(self):
        for floating_ip in self.nova.floating_ips.list():
            try:
                self.nova.floating_ips.delete(floating_ip)
            except NovaClientException:
                self.delete_floating_ip(floating_ip, use_neutron=True)

    def delete_servers(self):
        for server in self.nova.servers.list():
            try:
                self.nova.servers.delete(server)
            except NovaClientException:
                logger.info('nova server {} is not deletable'.format(server))

    def delete_keypairs(self):
        for key_pair in self.nova.keypairs.list():
            try:
                self.nova.keypairs.delete(key_pair)
            except NovaClientException:
                logger.info('key pair {} is not deletable'.format(key_pair.id))

    def delete_security_groups(self):
        for sg in self.nova.security_groups.list():
            if sg.description == 'Default security group':
                continue
            try:
                self.nova.security_groups.delete(sg)
            except NovaClientException:
                logger.info(
                    'The Security Group {} is not deletable'.format(sg))

    def delete_ports(self, networks):
        # After some experiments the following sequence for deletion was found
        # router_interface and ports -> subnets -> routers -> nets
        # Delete router interface and ports
        # TBD some ports are still kept after the cleanup.
        # Need to find why and delete them as well
        # But it does not fail the execution so far.
        for port in self.neutron.list_ports()['ports']:
            if port['network_id'] not in networks:
                continue
            try:
                # TBD Looks like the port might be used either by router or
                # l3 agent
                # in case of router this condition is true
                # port['network'] == 'router_interface'
                # dunno what will happen in case of the l3 agent
                for fixed_ip in port['fixed_ips']:
                    self.neutron.remove_interface_router(
                        port['device_id'],
                        {
                            'router_id': port['device_id'],
                            'subnet_id': fixed_ip['subnet_id'],
                        }
                    )
            except NeutronClientException:
                logger.info('the port {} is not deletable'
                            .format(port['id']))

    def cleanup_network(self, networks_to_skip=tuple()):
        """Clean up the neutron networks.

        :param networks_to_skip: list of networks names that should be kept
        """
        # net ids with the names from networks_to_skip are filtered out
        networks = [x['id'] for x in self.neutron.list_networks()['networks']
                    if x['name'] not in networks_to_skip]

        self.delete_keypairs()

        self.delete_floating_ips()

        self.delete_servers()

        self.delete_security_groups()

        self.delete_ports(networks)

        self.delete_subnets(networks)

        self.delete_routers()

        # Delete nets
        for net in networks:
            try:
                self.neutron.delete_network(net)
            except NeutronClientException:
                logger.info('the net {} is not deletable'
                            .format(net))

    def execute_through_host(self, ssh, vm_host, cmd, creds=()):
        logger.debug("Making intermediate transport")
        intermediate_transport = ssh._ssh.get_transport()

        logger.debug("Opening channel to VM")
        intermediate_channel = intermediate_transport.open_channel(
            'direct-tcpip', (vm_host, 22), (ssh.host, 0))
        logger.debug("Opening paramiko transport")
        transport = paramiko.Transport(intermediate_channel)
        logger.debug("Starting client")
        transport.start_client()
        logger.info("Passing authentication to VM: {}".format(creds))
        if not creds:
            creds = ('cirros', 'cubswin:)')
        transport.auth_password(creds[0], creds[1])

        logger.debug("Opening session")
        channel = transport.open_session()
        logger.info("Executing command: {}".format(cmd))
        channel.exec_command(cmd)

        result = {
            'stdout': [],
            'stderr': [],
            'exit_code': 0
        }

        logger.debug("Receiving exit_code")
        result['exit_code'] = channel.recv_exit_status()
        logger.debug("Receiving stdout")
        result['stdout'] = channel.recv(1024)
        logger.debug("Receiving stderr")
        result['stderr'] = channel.recv_stderr(1024)

        logger.debug("Closing channel")
        channel.close()

        return result

    def ssh_to_instance(self, env, vm, vm_keypair=None, username='cirros',
                        password=None, proxy_node=None):
        """Returns direct ssh client to instance via proxy"""
        logger.debug('Try to connect to vm {0}'.format(vm.name))
        net_name = [x for x in vm.addresses if len(vm.addresses[x]) > 0][0]
        vm_ip = vm.addresses[net_name][0]['addr']
        vm_mac = vm.addresses[net_name][0]['OS-EXT-IPS-MAC:mac_addr']
        net_id = self.neutron.list_ports(
            mac_address=vm_mac)['ports'][0]['network_id']
        dhcp_namespace = "qdhcp-{0}".format(net_id)
        if proxy_node is None:
            proxy_nodes = self.get_node_with_dhcp_for_network(net_id)
            if not proxy_nodes:
                raise Exception("Nodes with dhcp for network with id:{}"
                                " not found.".format(net_id))
        else:
            proxy_nodes = [proxy_node]

        proxy_commands = []
        for node in proxy_nodes:
            ip = env.find_node_by_fqdn(node).data['ip']
            key_paths = env.admin_ssh_keys_paths
            proxy_command = (
                "ssh {keys} -o 'StrictHostKeyChecking no' "
                "root@{node_ip} 'ip netns exec {ns} "
                "nc {vm_ip} 22'".format(
                    keys=' '.join('-i {}'.format(k) for k in key_paths),
                    ns=dhcp_namespace,
                    node_ip=ip,
                    vm_ip=vm_ip))
            proxy_commands.append(proxy_command)
        instance_keys = []
        if vm_keypair is not None:
            instance_keys.append(paramiko.RSAKey.from_private_key(
                six.StringIO(vm_keypair.private_key)))
        return SSHClient(vm_ip, port=22, username=username, password=password,
                         private_keys=instance_keys,
                         proxy_commands=proxy_commands)

    def wait_agents_alive(self, agt_ids_to_check):
        wait(lambda: all(agt['alive'] for agt in
                         self.neutron.list_agents()['agents']
                         if agt['id'] in agt_ids_to_check),
             timeout_seconds=5 * 60,
             waiting_for='agents is alive')

    def wait_agents_down(self, agt_ids_to_check):
        wait(lambda: all(not agt['alive'] for agt in
                         self.neutron.list_agents()['agents']
                         if agt['id'] in agt_ids_to_check),
             timeout_seconds=5 * 60,
             waiting_for='agents go down')

    def add_net(self, router_id):
        i = len(self.neutron.list_networks()['networks']) + 1
        network = self.create_network(name='net%02d' % i)['network']
        logger.info('network {name}({id}) is created'.format(**network))
        subnet = self.create_subnet(
            network_id=network['id'],
            name='net%02d__subnet' % i,
            cidr="192.168.%d.0/24" % i)
        logger.info('subnet {name}({id}) is created'.format(
            **subnet['subnet']))
        self.router_interface_add(
            router_id=router_id,
            subnet_id=subnet['subnet']['id'])
        return network['id']

    def add_server(self, network_id, key_name, hostname, sg_id):
        i = len(self.nova.servers.list()) + 1
        zone = self.nova.availability_zones.find(zoneName="nova")
        srv = self.create_server(
            name='server%02d' % i,
            availability_zone='{}:{}'.format(zone.zoneName, hostname),
            key_name=key_name,
            nics=[{'net-id': network_id}],
            security_groups=[sg_id])
        return srv

    def reschedule_router_to_primary_host(self, router_id, primary_host):
        agent_list = self.neutron.list_agents(
                          binary='neutron-l3-agent')['agents']
        agt_id_to_move_on = [agt['id'] for agt in agent_list
                             if agt['host'] == primary_host][0]
        self.force_l3_reschedule(router_id, agt_id_to_move_on)

    def force_l3_reschedule(self, router_id, new_l3_agt_id=None,
                            current_l3_agt_id=None):
        logger.info('going to reschedule the router on new agent')
        if current_l3_agt_id is None:
            l3_agents = self.neutron.list_l3_agent_hosting_routers(
                                     router_id)['agents']
            if len(l3_agents) != 1:
                raise Exception("Can't determine l3 agent to move router from")
            current_l3_agt_id = l3_agents[0]['id']
        if new_l3_agt_id is None:
            all_l3_agts = self.neutron.list_agents(
                              binary='neutron-l3-agent')['agents']
            available_l3_agts = [agt for agt in all_l3_agts
                                 if agt['id'] != current_l3_agt_id]
            new_l3_agt_id = available_l3_agts[0]['id']
        self.neutron.remove_router_from_l3_agent(current_l3_agt_id,
                                                 router_id)
        self.neutron.add_router_to_l3_agent(new_l3_agt_id,
                                            {"router_id": router_id})

        wait(lambda: self.neutron.list_l3_agent_hosting_routers(router_id),
             timeout_seconds=5 * 60, waiting_for="router moved to new agent")

    def reschedule_dhcp_agent(self, net_id, controller_fqdn):
        agent_list = self.neutron.list_agents(
            binary='neutron-dhcp-agent')['agents']
        agt_id_to_move_on = [agt['id'] for agt in agent_list
                             if agt['host'] == controller_fqdn][0]
        self.force_dhcp_reschedule(net_id, agt_id_to_move_on)

    def force_dhcp_reschedule(self, net_id, new_dhcp_agt_id):
        logger.info('going to reschedule network to specified '
                    'controller dhcp agent')
        current_dhcp_agt_id = self.neutron.list_dhcp_agent_hosting_networks(
            net_id)['agents'][0]['id']
        self.neutron.remove_network_from_dhcp_agent(current_dhcp_agt_id,
                                                    net_id)
        self.neutron.add_network_to_dhcp_agent(new_dhcp_agt_id,
                                               {'network_id': net_id})
        wait(lambda: self.neutron.list_dhcp_agent_hosting_networks(net_id),
             timeout_seconds=5 * 60,
             waiting_for="network reschedule to new dhcp agent")

    def _get_controller(self):
        # TODO(gdyuldin) remove this methods after moving to functions.os_cli
        return self.env.get_nodes_by_role('controller')[0]

    def tenant_create(self, name):
        # TODO(gdyuldin) remove this methods after moving to functions.os_cli
        with self._get_controller().ssh() as remote:
            return os_cli.OpenStack(remote).tenant_create(name=name)

    def tenant_delete(self, name):
        # TODO(gdyuldin) remove this methods after moving to functions.os_cli
        with self._get_controller().ssh() as remote:
            return os_cli.OpenStack(remote).tenant_delete(name=name)

    def user_create(self, name, password, tenant=None):
        # TODO(gdyuldin) remove this methods after moving to functions.os_cli
        with self._get_controller().ssh() as remote:
            return os_cli.OpenStack(remote).user_create(name=name,
                                                       password=password,
                                                       tenant=tenant)

    def user_delete(self, name):
        # TODO(gdyuldin) remove this methods after moving to functions.os_cli
        with self._get_controller().ssh() as remote:
            return os_cli.OpenStack(remote).user_delete(name=name)

    def server_hard_reboot(self, server):
        try:
            self.nova.servers.reboot(server.id, reboot_type='HARD')
        except NovaClientException:
            logger.info("nova server {} can't be rebooted".format(server))

    def server_start(self, server):
        try:
            self.nova.servers.start(server.id)
        except NovaClientException:
            logger.info("nova server {} can't be started".format(server))

    def server_stop(self, server):
        try:
            self.nova.servers.stop(server.id)
        except NovaClientException:
            logger.info("nova server {} can't be stopped".format(server))

    def rebuild_server(self, server, image):
        srv = server.rebuild(image)
        wait(lambda: self.nova.servers.get(srv).status == 'REBUILD',
             timeout_seconds=60, waiting_for='start of instance rebuild')
        return srv

    def rand_name(self, name):
        return name + '_' + str(random.randint(1, 0x7fffffff))

    def create_service(self, environment, session, json_data, to_json=True):
        service = self.murano.services.post(environment.id, path='/',
                                            data=json_data,
                                            session_id=session.id)
        if to_json:
            service = service.to_dict()
            service = json.dumps(service)
            return yaml.load(service)
        else:
            return service

    def wait_for_environment_deploy(self, environment):
        start_time = time.time()
        status = self.murano.environments.get(environment.id).status
        while status != 'ready' and time.time() - start_time < 3800:
            if status == 'deploy failure':
                return 0
            time.sleep(15)
            status = self.murano.environments.get(environment.id).status
        return self.murano.environments.get(environment.id)

    def deploy_environment(self, environment, session):
        self.murano.sessions.deploy(environment.id, session.id)
        return self.wait_for_environment_deploy(environment)

    def get_action_id(self, environment, name, service):
        env_data = environment.to_dict()
        a_dict = env_data['services'][service]['?']['_actions']
        for action_id, action in a_dict.iteritems():
            if action['name'] == name:
                return action_id

    def run_action(self, environment, action_id):
        self.murano.actions.call(environment.id, action_id)
        return self.wait_for_environment_deploy(environment)

    def status_check(self, environment, configurations, kubernetes=False,
                     negative=False):
        for configuration in configurations:
            if kubernetes:
                service_name = configuration[0]
                inst_name = configuration[1]
                ports = configuration[2:]
                ip = self.get_k8s_ip_by_instance_name(environment, inst_name,
                                                      service_name)
                if ip and ports and negative:
                    for port in ports:
                        assert self.check_port_access(ip, port, negative)
                        assert self.check_k8s_deployment(ip, port, negative)
                elif ip and ports:
                    for port in ports:
                        assert self.check_port_access(ip, port)
                        assert self.check_k8s_deployment(ip, port)
                else:
                    assert 0, "Instance {} doesn't have floating IP"\
                        .format(inst_name)
            else:
                inst_name = configuration[0]
                ports = configuration[1:]
                ip = self.get_ip_by_instance_name(environment, inst_name)
                if ip and ports:
                    for port in ports:
                        assert self.check_port_access(ip, port)
                else:
                    assert 0, "Instance {} doesn't have floating IP"\
                        .format(inst_name)

    def check_port_access(self, ip, port, negative=False):
        result = 1
        start_time = time.time()
        while time.time() - start_time < 600:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            result = sock.connect_ex((str(ip), port))
            sock.close()

            if result == 0 or negative:
                break
            time.sleep(5)
        if negative:
            assert result != 0, '{} port is opened on instance'.format(port)
        else:
            assert result == 0, '{} port is closed on instance'.format(port)
        return True

    def check_k8s_deployment(self, ip, port, timeout=3600, negative=False):
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                self.verify_connection(ip, port, negative)
                return True
            except RuntimeError:
                time.sleep(10)
        assert 0, 'Containers are not ready'

    def verify_connection(self, ip, port, negative=False):
        try:
            tn = telnetlib.Telnet(ip, port)
            tn.write('GET / HTTP/1.0\n\n')
            buf = tn.read_all()
            if negative and len(buf) == 0:
                return True
            elif len(buf) != 0:
                tn.sock.sendall(telnetlib.IAC + telnetlib.NOP)
                return True
            else:
                raise RuntimeError('Resource at {0}:{1} not exist'.
                                   format(ip, port))
        except socket.error as e:
            raise RuntimeError('Found reset: {0}'.format(e))

    def get_k8s_ip_by_instance_name(self, environment, inst_name,
                                    service_name):
        """Returns ip of specific kubernetes node (gateway, master, minion)
        based. Search depends on service name of kubernetes and names of
        spawned instances
        :param environment: Murano environment
        :param inst_name: Name of instance or substring of instance name
        :param service_name: Name of Kube Cluster application in Murano
        environment
        :return: Ip of Kubernetes instances
        """
        for service in environment.services:
            if service_name in service['name']:
                if "gateway" in inst_name:
                    for gateway in service['gatewayNodes']:
                        if inst_name in gateway['instance']['name']:
                            return gateway['instance']['floatingIpAddress']
                elif "master" in inst_name:
                    return service['masterNode']['instance'][
                        'floatingIpAddress']
                elif "minion" in inst_name:
                    for minion in service['minionNodes']:
                        if inst_name in minion['instance']['name']:
                            return minion['instance']['floatingIpAddress']

    def get_ip_by_instance_name(self, environment, inst_name):
        """Returns ip of instance using instance name
        :param environment: Murano environment
        :param name: String, which is substring of name of instance or name of
        instance
        :return:
        """
        for service in environment.services:
            if inst_name in service['instance']['name']:
                return service['instance']['floatingIpAddress']

    def get_environment(self, environment):
        return self.murano.environments.get(environment.id)

    def check_instance(self, instance_list, gateways_count, nodes_count):
        names = ["master-1", "minion-1", "gateway-1"]
        if gateways_count == 2:
            names.append("gateway-2")
        if nodes_count == 2:
            names.append("minion-2")
        count = 0
        for instance in instance_list:
            for name in names:
                if instance.name.find(name) > -1:
                    count += 1
                    assert instance.status == 'ACTIVE', \
                        "Instance {} is not in active status".format(name)
        assert count == len(names)
