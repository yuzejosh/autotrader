# Copyright 2021 Optiver Asia Pacific Pty. Ltd.
#
# This file is part of Ready Trader Go.
#
#     Ready Trader Go is free software: you can redistribute it and/or
#     modify it under the terms of the GNU Affero General Public License
#     as published by the Free Software Foundation, either version 3 of
#     the License, or (at your option) any later version.
#
#     Ready Trader Go is distributed in the hope that it will be useful,
#     but WITHOUT ANY WARRANTY; without even the implied warranty of
#     MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#     GNU Affero General Public License for more details.
#
#     You should have received a copy of the GNU Affero General Public
#     License along with Ready Trader Go.  If not, see
#     <https://www.gnu.org/licenses/>.
import asyncio
import itertools
import numpy as np
from collections import deque
import time

from typing import List

from ready_trader_go import BaseAutoTrader, Instrument, Lifespan, MAXIMUM_ASK, MINIMUM_BID, Side


LOT_SIZE = 10
POSITION_LIMIT = 100
TICK_SIZE_IN_CENTS = 100
MIN_BID_NEAREST_TICK = (MINIMUM_BID + TICK_SIZE_IN_CENTS) // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
MAX_ASK_NEAREST_TICK = MAXIMUM_ASK // TICK_SIZE_IN_CENTS * TICK_SIZE_IN_CENTS
NUM_SAMPLEPTS = 5
TRADE_LIMIT = 60


def midpoint(high, low):
    """Returns midpoint of best ask price and best bid price
    - to use with ETF"""
    return (high+low)/2

class AutoTrader(BaseAutoTrader):
    
    def __init__(self, loop: asyncio.AbstractEventLoop, team_name: str, secret: str):
        """Initialise a new instance of the AutoTrader class."""
        super().__init__(loop, team_name, secret)
        self.order_ids = itertools.count(1)
        self.bids = set()
        self.asks = set()
        self.FUT_position = 0

        self.ETFbest_ask = 0
        self.ETFbest_bid = 0
        self.FUTbest_ask = 0
        self.FUTbest_bid = 0

        self.ETF_mid = 0
        self.FUT_mid = 0
        self.ratio = deque([])
        self.loopcounter = 0
        
        self.ETF_mid_list = deque([])
        self.FUT_mid_list = deque([])
        self.zscore_list = deque([])
        self.pending_orders = []
        self.time_not_recorded = True
        self.time_10_unhedged = 0
        self.num_orders_made = 0
        
        

        self.ask_id = self.ask_price = self.bid_id = self.bid_price = self.position = 0

    def on_error_message(self, client_order_id: int, error_message: bytes) -> None:
        """Called when the exchange detects an error.

        If the error pertains to a particular order, then the client_order_id
        will identify that order, otherwise the client_order_id will be zero.
        """
        self.logger.warning("error with order %d: %s", client_order_id, error_message.decode())
        if client_order_id != 0 and (client_order_id in self.bids or client_order_id in self.asks):
            self.on_order_status_message(client_order_id, 0, 0, 0)

    def on_hedge_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your hedge orders is filled.

        The price is the average price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received hedge filled for order %d with average price %d and volume %d", client_order_id,
                         price, volume)

    def on_order_book_update_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                                     ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically to report the status of an order book.

        The sequence number can be used to detect missed or out-of-order
        messages. The five best available ask (i.e. sell) and bid (i.e. buy)
        prices are reported along with the volume available at each of those
        price levels.
        """
        self.logger.info("received order book for instrument %d with sequence number %d", instrument,
                         sequence_number)  
        # change so we trade (ETFS- less liquid) and hedge in futures
        
        # find and save midpoint (approx. value) of ETF/Fut
        
        global hedge_ratio
        start = time.time()
        
        if instrument == Instrument.ETF:
            self.ETFbest_ask = ask_prices[0]
            self.ETFbest_bid = bid_prices[0]
        elif instrument == Instrument.FUTURE:
            self.FUT_best_ask = ask_prices[0]
            self.FUT_best_bid = bid_prices[0]
        
            
        print("loop no: ", self.loopcounter)
        print("ETF POSITION: ",self.position)
        print("FUT POSITION: ",self.FUT_position)
        print("POS DIFF: ", abs(abs(self.position) - abs(self.FUT_position)))
        # checks if more than 10 unhedged lots have been held for more than a minute
        if abs(abs(self.position) - abs(self.FUT_position)) > 10 and self.time_not_recorded:
            self.time_10_unhedged = time.time()
            self.time_not_recorded = False
        #resets timer if difference goes back to accpetable level
        if abs(abs(self.position) - abs(self.FUT_position)) < 10 and self.time_not_recorded == False:
            self.time_10_unhedged = 0
            self.time_not_recorded = True
        if abs(abs(self.position) - abs(self.FUT_position)) > 10 and self.time_not_recorded == False:
            print("WARNING: more than 10 unhedged lots for: ",time.time() - self.time_10_unhedged, "secs")
            if time.time() - self.time_10_unhedged > 30:
                # case where ETF position is greater than FUT position
                # e.g ETF : 20 FUT : -10 --> SELL 20 FUT
                
                if self.position < 0 and self.FUT_position < 0:
                    self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, abs(abs(self.position) + abs(self.FUT_position)))
                    self.FUT_position += abs(abs(self.position) + abs(self.FUT_position))
                    self.time_10_unhedged = 0
                    self.time_not_recorded = True
                    
                    
                if self.position > 0 and self.FUT_position > 0:
                    self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, abs(abs(self.position) + abs(self.FUT_position)))
                    self.FUT_position -= abs(abs(self.position) + abs(self.FUT_position))
                    self.time_10_unhedged = 0
                    self.time_not_recorded = True
                    
                
                if abs(self.position) > abs(self.FUT_position):
                    if self.position < 0:
                        self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, abs(abs(self.position) - abs(self.FUT_position)))
                        self.FUT_position += abs(abs(self.position) - abs(self.FUT_position))
                        self.time_10_unhedged = 0
                        self.time_not_recorded = True
                    else:
                        self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, abs(abs(self.position) - abs(self.FUT_position)))
                        self.FUT_position -= abs(abs(self.position) - abs(self.FUT_position))
                        self.time_10_unhedged = 0
                        self.time_not_recorded = True
                # case where FUT position is greater than ETF position
                # e.g ETF : -50 FUT : 30 --> BUY 20 FUT
                elif abs(self.position) < abs(self.FUT_position):
                    if self.position < 0:
                        self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, abs(abs(self.position) - abs(self.FUT_position)))
                        self.FUT_position -= abs(abs(self.position) - abs(self.FUT_position))
                        self.time_10_unhedged = 0
                        self.time_not_recorded = True
                    elif self.position > 0:
                        self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, abs(abs(self.position) - abs(self.FUT_position)))
                        self.FUT_position += abs(abs(self.position) - abs(self.FUT_position))
                        self.time_10_unhedged = 0
                        self.time_not_recorded = True
                        
        #print("pending order IDs:", self.pending_orders)
        
        # cancel all pending orders when list of pending orders is too long
        #print(self.pending_orders)
        if len(self.pending_orders) > 5 or self.position > 60 or self.position < -60:
            for orderIDs in self.pending_orders:
                self.send_cancel_order(orderIDs)
            self.pending_orders = []
            print("orders cancelled!")
            self.num_orders_made = 0
        
        if self.ETFbest_ask != 0 and self.FUT_best_ask != 0:
            #calculate the midprice of ETF
            self.ETF_mid = midpoint(self.ETFbest_ask, self.ETFbest_bid)/100
            #create a rolling list of midprices of ETF
            self.ETF_mid_list.append(self.ETF_mid)
            if len(self.ETF_mid_list) > 50:
                self.ETF_mid_list.popleft()
            
            #calculate the midprice of FUT
            self.FUT_mid = midpoint(self.FUT_best_ask, self.FUT_best_bid)/100
            #create a rolling list of midprices of FUT
            self.FUT_mid_list.append(self.FUT_mid)
            if len(self.FUT_mid_list) > 50:
                self.FUT_mid_list.popleft()
            
            #calculate the ratio of ETF to FUT
            current_ratio = (self.ETF_mid) / (self.FUT_mid)
            #create a rolling list of ratios of ETF to FUT
            self.ratio.append(current_ratio)
            if len(self.ratio) > 50:
                self.ratio.popleft()
            
        if self.loopcounter > 30:
            ETF_dev = np.std(self.ETF_mid_list)
            #print("ETF_dev is: ", ETF_dev)
            FUT_dev = np.std(self.FUT_mid_list)
            #print("FUT_dev is : ", FUT_dev)
            correlation = np.corrcoef(self.ETF_mid_list, self.FUT_mid_list)[0, 1]
            if ETF_dev == 0 or FUT_dev == 0:
                hedge_ratio = 1
            else:
                hedge_ratio = correlation * (ETF_dev / FUT_dev)
            #print("HEDGE RATIO IS:", hedge_ratio)
            
            #print("RATIO LIST: ", self.ratio)
            zscore = (current_ratio - np.mean(self.ratio))/ np.std(self.ratio)
            #print("ZSCORE IS: ", zscore)
            #print("RATIO IS: ", current_ratio)
            self.zscore_list.append(zscore)
            #print(list(self.zscore_list))
            if len(self.zscore_list) > 10:
                self.zscore_list.popleft()
                
                x = np.array([0,1,2,3,4,5,6,7,8,9])
                A = np.vstack([x, np.ones(len(x))]).T
                y = np.array(self.zscore_list)
                
                zscore_slope, intercept = np.linalg.lstsq(A, y, rcond=None)[0]
                #print("ZSCORE SLOPE IS: ", zscore_slope)
                
                #print("position: ", self.position)
                if self.position > TRADE_LIMIT and self.num_orders_made <= 2:
                    self.ask_id = next(self.order_ids)
                    self.send_insert_order(self.ask_id, Side.SELL, self.ETFbest_ask, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                    self.asks.add(self.ask_id)
                    self.pending_orders.append(self.ask_id)
                    self.num_orders_made += 1
                if self.position < -TRADE_LIMIT and self.num_orders_made <= 2:
                    self.bid_id = next(self.order_ids)
                    self.send_insert_order(self.bid_id, Side.BUY, self.ETFbest_bid, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                    self.bids.add(self.bid_id)
                    self.pending_orders.append(self.bid_id)
                    self.num_orders_made += 1
                if self.position < TRADE_LIMIT and self.position > -TRADE_LIMIT:
                    self.num_orders_made = 0
                if self.FUT_position > TRADE_LIMIT:
                    self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, LOT_SIZE)
                    self.FUT_position -= LOT_SIZE
                if self.FUT_position < -TRADE_LIMIT:
                    self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, LOT_SIZE)
                    self.FUT_position += LOT_SIZE
                    

                
                
                if zscore_slope > 0 and zscore > 1 and self.position > -TRADE_LIMIT:
                    '''sell if zscore slope is -ve'''
                    self.ask_id = next(self.order_ids)
                    self.send_insert_order(self.ask_id, Side.SELL, self.ETFbest_ask, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                    self.asks.add(self.ask_id)
                    self.pending_orders.append(self.ask_id)
                    print("made SELL order for 10 ETFS")
                elif zscore_slope < 0 and zscore < -1 and self.position < TRADE_LIMIT:
                    '''buy if zscore slope is +ve'''
                    self.bid_id = next(self.order_ids)
                    self.send_insert_order(self.bid_id, Side.BUY, self.ETFbest_bid, LOT_SIZE, Lifespan.GOOD_FOR_DAY)
                    self.bids.add(self.bid_id)
                    self.pending_orders.append(self.bid_id)
                    print("made BUY order for 10 ETFS")


                    
        
        
        end = time.time()
        time_elapsed = end - start
        print(time_elapsed * 1000, "ms")
        
        self.loopcounter += 1


    def on_order_filled_message(self, client_order_id: int, price: int, volume: int) -> None:
        """Called when one of your orders is filled, partially or fully.

        The price is the price at which the order was (partially) filled,
        which may be better than the order's limit price. The volume is
        the number of lots filled at that price.
        """
        self.logger.info("received order filled for order %d with price %d and volume %d", client_order_id,
                         price, volume)
        if client_order_id in self.bids:
            self.position += volume
            if client_order_id in self.pending_orders:
                self.pending_orders.remove(client_order_id)
            if int(volume * abs(hedge_ratio)) != 0:
                # SELL FUT TO HEDGE
                self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, int(volume * abs(hedge_ratio)))
                self.FUT_position -= int(volume * abs(hedge_ratio))
            else:
                self.send_hedge_order(next(self.order_ids), Side.ASK, MIN_BID_NEAREST_TICK, 1)
                self.FUT_position -= 1
        elif client_order_id in self.asks:
            self.position -= volume
            if client_order_id in self.pending_orders:
                self.pending_orders.remove(client_order_id)
            if int(volume * abs(hedge_ratio)) != 0:
                # BUY FUT TO HEDGE
                self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, int(volume * abs(hedge_ratio)))
                self.FUT_position += int(volume * abs(hedge_ratio))
            else:
                self.send_hedge_order(next(self.order_ids), Side.BID, MAX_ASK_NEAREST_TICK, 1)
                self.FUT_position += 1


    def on_order_status_message(self, client_order_id: int, fill_volume: int, remaining_volume: int,
                                fees: int) -> None:
        """Called when the status of one of your orders changes.

        The fill_volume is the number of lots already traded, remaining_volume
        is the number of lots yet to be traded and fees is the total fees for
        this order. Remember that you pay fees for being a market taker, but
        you receive fees for being a market maker, so fees can be negative.

        If an order is cancelled its remaining volume will be zero.
        """
        self.logger.info("received order status for order %d with fill volume %d remaining %d and fees %d",
                         client_order_id, fill_volume, remaining_volume, fees)
        if remaining_volume == 0:
            if client_order_id == self.bid_id:
                self.bid_id = 0
            elif client_order_id == self.ask_id:
                self.ask_id = 0

            # It could be either a bid or an ask
            self.bids.discard(client_order_id)
            self.asks.discard(client_order_id)

    def on_trade_ticks_message(self, instrument: int, sequence_number: int, ask_prices: List[int],
                               ask_volumes: List[int], bid_prices: List[int], bid_volumes: List[int]) -> None:
        """Called periodically when there is trading activity on the market.

        The five best ask (i.e. sell) and bid (i.e. buy) prices at which there
        has been trading activity are reported along with the aggregated volume
        traded at each of those price levels.

        If there are less than five prices on a side, then zeros will appear at
        the end of both the prices and volumes arrays.
        """
        self.logger.info("received trade ticks for instrument %d with sequence number %d", instrument,
                         sequence_number)
