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
import uuid


@pytest.mark.parametrize('cluster', [{'initial_gateways': 1, 'max_gateways': 2,
                                     'initial_nodes': 2, 'max_nodes': 2}],
                         indirect=['cluster'])
@pytest.mark.run(order=1)
@pytest.mark.testrail_id('543015')
def test_kub_scale_down(environment, murano, session, cluster, grafana,
                        influx):
    """Check ScaleNodesDown action for Kubernetes Cluster
    Scenario:
        1. Create Murano environment
        2. Add Kubernetes Cluster application to the environment: Set
        initial_gateways=1, max_gateways=2, initial_nodes=2, max_nodes=2
        3. Add Kubernetes Pod to the environment
        4. Add some application to the environment
        5. Deploy environment
        6. Check deployment status and make sure that all nodes are active
        7. Execute ScaleNodesDown action
        8. Check deployment status and make sure that all nodes are active
        9. Delete Murano environment
    """
    deployed_environment = murano.deploy_environment(environment, session)
    murano.check_instance(gateways_count=1, nodes_count=2)
    murano.status_check(deployed_environment,
                        [[cluster['name'], "master-1", 8080],
                         # [cluster['name'], "gateway-1", 8083],
                         [cluster['name'], "minion-1", 4194],
                         [cluster['name'], "minion-2", 4194]
                         ],
                        kubernetes=True)

    action_id = murano.get_action_id(
        deployed_environment, 'scaleNodesDown', 0)
    deployed_environment = murano.run_action(deployed_environment, action_id)
    murano.check_instance(gateways_count=1, nodes_count=1)
    murano.status_check(deployed_environment,
                        [[cluster['name'], "master-1", 8080],
                         # [cluster['name'], "gateway-1", 8083],
                         [cluster['name'], "minion-1", 4194]
                         ],
                        kubernetes=True)


@pytest.mark.parametrize('cluster', [{'initial_gateways': 1, 'max_gateways': 2,
                                     'initial_nodes': 1, 'max_nodes': 2}],
                         indirect=['cluster'])
@pytest.mark.run(order=2)
@pytest.mark.testrail_id('543014')
def test_kub_scale_up(murano, environment, session, cluster, grafana, influx):
    """Check ScaleNodesUp action for Kubernetes Cluster
    Scenario:
        1. Create Murano environment
        2. Add Kubernetes Cluster application to the environment: Set
        initial_gateways=1, max_gateways=2, initial_nodes=1, max_nodes=2
        3. Add Kubernetes Pod to the environment
        4. Add some application to the environment
        5. Deploy environment
        6. Check deployment status and make sure that all nodes are active
        7. Execute ScaleNodesUp action
        8. Check deployment status and make sure that all nodes are active
        9. Delete Murano environment
    """
    deployed_environment = murano.deploy_environment(environment, session)
    murano.check_instance(gateways_count=1, nodes_count=1)
    murano.status_check(deployed_environment,
                        [[cluster['name'], "master-1", 8080],
                         # [cluster['name'], "gateway-1", 8083],
                         [cluster['name'], "minion-1", 4194]
                         ],
                        kubernetes=True)
    action_id = murano.get_action_id(deployed_environment, 'scaleNodesUp', 0)
    deployed_environment = murano.run_action(deployed_environment, action_id)
    murano.check_instance(gateways_count=1, nodes_count=2)
    murano.status_check(deployed_environment,
                        [[cluster['name'], "master-1", 8080],
                         # [cluster['name'], "gateway-1", 8083],
                         [cluster['name'], "minion-1", 4194],
                         [cluster['name'], "minion-2", 4194]
                         ],
                        kubernetes=True)


@pytest.mark.parametrize('cluster', [{'initial_gateways': 2, 'max_gateways': 2,
                                     'initial_nodes': 1, 'max_nodes': 2}],
                         indirect=['cluster'])
@pytest.mark.run(order=3)
@pytest.mark.testrail_id('638363')
def test_kub_gateway_down(murano, environment, session, cluster, grafana,
                          influx):
    """Check ScaleGatewaysDown action for Kubernetes Cluster
    Scenario:
        1. Create Murano environment
        2. Add Kubernetes Cluster application to the environment: Set
        initial_gateways=2, max_gateways=2, initial_nodes=1, max_nodes=2
        3. Add Kubernetes Pod to the environment
        4. Add some application to the environment
        5. Deploy environment
        6. Check deployment status and make sure that all nodes are active
        7. Execute scaleGatewaysDown action
        8. Check deployment status and make sure that all nodes are active
        9. Delete Murano environment
    """
    deployed_environment = murano.deploy_environment(environment, session)
    murano.check_instance(gateways_count=2, nodes_count=1)
    murano.status_check(deployed_environment,
                        [[cluster['name'], "master-1", 8080],
                         # [cluster['name'], "gateway-1", 8083],
                         # [cluster['name'], "gateway-2", 8083],
                         [cluster['name'], "minion-1", 4194]
                         ],
                        kubernetes=True)

    action_id = murano.get_action_id(deployed_environment, 'scaleGatewaysDown',
                                     0)
    deployed_environment = murano.run_action(deployed_environment, action_id)
    murano.check_instance(gateways_count=1, nodes_count=1)
    murano.status_check(deployed_environment,
                        [[cluster['name'], "master-1", 8080],
                         # [cluster['name'], "gateway-1", 8083],
                         [cluster['name'], "minion-1", 4194]
                         ],
                        kubernetes=True)


@pytest.mark.parametrize('cluster', [{'initial_gateways': 1, 'max_gateways': 2,
                                     'initial_nodes': 1, 'max_nodes': 2}],
                         indirect=['cluster'])
@pytest.mark.run(order=4)
@pytest.mark.testrail_id('543016')
def test_kub_gateway_up(murano, environment, session, cluster, grafana,
                        influx):
    """Check ScaleGatewaysUp action for Kubernetes Cluster
    Scenario:
        1. Create Murano environment
        2. Add Kubernetes Cluster application to the environment: Set
        initial_gateways=1, max_gateways=2, initial_nodes=1, max_nodes=2
        3. Add Kubernetes Pod to the environment
        4. Add some application to the environment
        5. Deploy environment
        6. Check deployment status and make sure that all nodes are active
        7. Execute scaleGatewaysUp action
        8. Check deployment status and make sure that all nodes are active
        9. Delete Murano environment
    """
    deployed_environment = murano.deploy_environment(environment, session)
    murano.check_instance(gateways_count=1, nodes_count=1)
    murano.status_check(deployed_environment,
                        [[cluster['name'], "master-1", 8080],
                         # [cluster['name'], "gateway-1", 8083],
                         [cluster['name'], "minion-1", 4194]
                         ],
                        kubernetes=True)
    action_id = murano.get_action_id(deployed_environment, 'scaleGatewaysUp',
                                     0)
    deployed_environment = murano.run_action(deployed_environment, action_id)
    murano.check_instance(gateways_count=2, nodes_count=1)
    murano.status_check(deployed_environment,
                        [[cluster['name'], "master-1", 8080],
                         # [cluster['name'], "gateway-1", 8083],
                         # [cluster['name'], "gateway-2", 8083],
                         [cluster['name'], "minion-1", 4194]
                         ],
                        kubernetes=True)


@pytest.mark.run(order=5)
@pytest.mark.testrail_id('543022')
def test_kub_nodes_up_if_limit_reached(murano, environment, session, cluster,
                                       grafana, influx):
    """Check ScaleNodesUp and scaleGatewaysUp actions for Kubernetes Cluster
    if maximum nodes limit is already reached
    Scenario:
        1. Create Murano environment
        2. Add Kubernetes Cluster application to the environment: Set
        initial_gateways=1, max_gateways=1, initial_nodes=1, max_nodes=1
        3. Add Kubernetes Pod to the environment
        4. Add some application to the environment
        5. Deploy environment
        6. Check deployment status and make sure that all nodes are active
        7. Execute scaleNodesUp action
        8. Check error message
        9. Execute scaleGatewaysUp action
        10. Check error message
        11. Delete Murano environment
    """
    deployed_environment = murano.deploy_environment(environment, session)
    murano.check_instance(gateways_count=1, nodes_count=1)
    murano.status_check(deployed_environment,
                        [[cluster['name'], "master-1", 8080],
                        # [cluster['name'], "gateway-1", 8083],
                         [cluster['name'], "minion-1", 4194]
                         ],
                        kubernetes=True)
    action_id = murano.get_action_id(
        deployed_environment, 'scaleNodesUp', 0)
    deployed_environment = murano.run_action(deployed_environment, action_id)
    murano.check_instance(gateways_count=1, nodes_count=1)
    logs = murano.get_log(deployed_environment)
    assert 'Action scaleNodesUp is scheduled' in logs
    # assert 'The maximum number of nodes has been reached' in logs
    murano.check_instance(gateways_count=1, nodes_count=1)
    action_id = murano.get_action_id(
        deployed_environment, 'scaleGatewaysUp', 0)
    deployed_environment = murano.run_action(deployed_environment, action_id)
    murano.check_instance(gateways_count=1, nodes_count=1)
    logs = murano.get_log(deployed_environment)
    assert 'Action scaleGatewaysUp is scheduled' in logs
    # assert 'The maximum number of gateway nodes has been reached' in logs
