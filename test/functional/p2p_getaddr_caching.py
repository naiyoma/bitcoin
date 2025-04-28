#!/usr/bin/env python3
# Copyright (c) 2020-2022 The Bitcoin Core developers
# Distributed under the MIT software license, see the accompanying
# file COPYING or http://www.opensource.org/licenses/mit-license.php.
"""Test addr response caching"""

import time

from test_framework.p2p import (
    P2PInterface,
    p2p_lock
)
from test_framework.test_framework import BitcoinTestFramework
from test_framework.util import (
    assert_equal,
    p2p_port,
)

# As defined in net_processing.
MAX_ADDR_TO_SEND = 1000
MAX_PCT_ADDR_TO_SEND = 23


class AddrReceiver(P2PInterface):

    def __init__(self):
        super().__init__()
        self.received_addrs = None
        self.received_addrs_with_time = None

    def get_received_addrs(self):
        with p2p_lock:
            return self.received_addrs

    def get_received_addrs_with_time(self):
        with p2p_lock:
            return self.received_addrs_with_time
        
    def on_addr(self, message):
        self.received_addrs = []
        self.received_addrs_with_time = {}
        for addr in message.addrs:
            self.received_addrs.append(addr.ip)
            self.received_addrs_with_time[addr.ip] = addr.time

    def addr_received(self):
        return self.received_addrs is not None


class AddrTest(BitcoinTestFramework):
    def set_test_params(self):
        self.num_nodes = 1
        # Use some of the remaining p2p ports for the onion binds.
        self.onion_port1 = p2p_port(self.num_nodes)
        self.onion_port2 = p2p_port(self.num_nodes + 1)
        self.extra_args = [
            [f"-bind=127.0.0.1:{self.onion_port1}=onion", f"-bind=127.0.0.1:{self.onion_port2}=onion"],
        ]

    def run_test(self):
        self.log.info('Fill peer AddrMan with a lot of records')
        for i in range(10000):
            first_octet = i >> 8
            second_octet = i % 256
            a = "{}.{}.1.1".format(first_octet, second_octet)
            self.nodes[0].addpeeraddress(a, 8333)

        # Need to make sure we hit MAX_ADDR_TO_SEND records in the addr response later because
        # only a fraction of all known addresses can be cached and returned.
        assert len(self.nodes[0].getnodeaddresses(0)) > int(MAX_ADDR_TO_SEND / (MAX_PCT_ADDR_TO_SEND / 100))

        last_response_on_local_bind = None
        last_response_on_onion_bind1 = None
        last_response_on_onion_bind2 = None
        self.log.info('Send many addr requests within short time to receive same response')
        N = 5
        cur_mock_time = int(time.time())
        for i in range(N):
            addr_receiver_local = self.nodes[0].add_p2p_connection(AddrReceiver())
            addr_receiver_onion1 = self.nodes[0].add_p2p_connection(AddrReceiver(), dstport=self.onion_port1)
            addr_receiver_onion2 = self.nodes[0].add_p2p_connection(AddrReceiver(), dstport=self.onion_port2)

            # Trigger response
            cur_mock_time += 5 * 60
            self.nodes[0].setmocktime(cur_mock_time)
            addr_receiver_local.wait_until(addr_receiver_local.addr_received)
            addr_receiver_onion1.wait_until(addr_receiver_onion1.addr_received)
            addr_receiver_onion2.wait_until(addr_receiver_onion2.addr_received)

            if i > 0:
                # Responses from different binds should be unique
                assert last_response_on_local_bind != addr_receiver_onion1.get_received_addrs()
                assert last_response_on_local_bind != addr_receiver_onion2.get_received_addrs()
                assert last_response_on_onion_bind1 != addr_receiver_onion2.get_received_addrs()
                # Responses on from the same bind should be the same
                assert_equal(last_response_on_local_bind, addr_receiver_local.get_received_addrs())
                assert_equal(last_response_on_onion_bind1, addr_receiver_onion1.get_received_addrs())
                assert_equal(last_response_on_onion_bind2, addr_receiver_onion2.get_received_addrs())

            last_response_on_local_bind = addr_receiver_local.get_received_addrs()
            last_response_on_onion_bind1 = addr_receiver_onion1.get_received_addrs()
            last_response_on_onion_bind2 = addr_receiver_onion2.get_received_addrs()

            for response in [last_response_on_local_bind, last_response_on_onion_bind1, last_response_on_onion_bind2]:
                assert_equal(len(response), MAX_ADDR_TO_SEND)

        cur_mock_time += 3 * 24 * 60 * 60
        self.nodes[0].setmocktime(cur_mock_time)

        self.log.info('After time passed, see a new response to addr request')
        addr_receiver_local = self.nodes[0].add_p2p_connection(AddrReceiver())
        addr_receiver_onion1 = self.nodes[0].add_p2p_connection(AddrReceiver(), dstport=self.onion_port1)
        addr_receiver_onion2 = self.nodes[0].add_p2p_connection(AddrReceiver(), dstport=self.onion_port2)

        # Trigger response
        cur_mock_time += 5 * 60
        self.nodes[0].setmocktime(cur_mock_time)
        addr_receiver_local.wait_until(addr_receiver_local.addr_received)
        addr_receiver_onion1.wait_until(addr_receiver_onion1.addr_received)
        addr_receiver_onion2.wait_until(addr_receiver_onion2.addr_received)

        # new response is different
        assert set(last_response_on_local_bind) != set(addr_receiver_local.get_received_addrs())
        assert set(last_response_on_onion_bind1) != set(addr_receiver_onion1.get_received_addrs())
        assert set(last_response_on_onion_bind2) != set(addr_receiver_onion2.get_received_addrs())


        # test to verify timestamp randomization
        self.log.info('Test that timestamps are randomized when getting addresses')
        
        # get some addresses directly from addrman
        original_addrs = self.nodes[0].getnodeaddresses(20)
        original_timestamps = {addr['address']: addr['time'] for addr in original_addrs}
        
        
        addr_receiver = self.nodes[0].add_p2p_connection(AddrReceiver())
        cur_mock_time = int(time.time())
        self.nodes[0].setmocktime(cur_mock_time + 5 * 60)
        addr_receiver.wait_until(addr_receiver.addr_received)
        
        # check if the addresses we received have different timestamps
        
        received_addrs = addr_receiver.get_received_addrs_with_time()
        
        # Check that timestamps have been modified by randomization
        for addr, timestamp in received_addrs.items():
            if addr in original_timestamps:
                # import pdb; pdb.set_trace()
                assert original_timestamps[addr] != timestamp, f"Timestamp for {addr} was not randomized"
                # The difference should be between 0 and 10 seconds
                diff = timestamp - original_timestamps[addr]
                assert 0 < abs(diff) <= 20, f"Timestamp randomization for {addr} outside expected range: {diff} seconds"


        # self.test_same_peer_multiple_networks()


if __name__ == '__main__':
    AddrTest(__file__).main()
