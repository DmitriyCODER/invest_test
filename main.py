import os
import sys
import time
from decimal import Decimal

import tinvest
from configparser import ConfigParser
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

from tinvest import SandboxRegisterRequest, SandboxSetPositionBalanceRequest, SandboxSetCurrencyBalanceRequest, \
    MarketOrderRequest, CandleResolution

TOKEN = "t.iX0WoMHy3p6uJKMs7fTFt2ZtvUYzRamtGj3JPAjCoyNPy13SI_KF7fvTuHSQuA9ojWjzUc96R0UTYWPPaimQNQ"
BASE_DIR = os.path.abspath(os.path.dirname(sys.argv[0]))


def get_candle_interval(config_value):
    candle_interval_list = ['1min', '2min', '3min', '5min', '10min', '15min', '30min',
                            'hour', 'day', 'week', 'month']
    candle_interval_dict = {'1': '1min', '2': '2min', '3': '3min', '5': '5min', '10': '10min',
                            '15': '15min', '30': '30min', '60': 'hour', '1440': 'day', '10080': 'week',
                            '40320': 'month', '41760': 'month', '43200': 'month', '44640': 'month'}
    if config_value in candle_interval_list:
        return config_value
    elif config_value in candle_interval_dict:
        return candle_interval_dict[config_value]
    else:
        return '1min'


def get_poll_interval(poll_interval: str):
    if poll_interval.isdigit():
        return timedelta(minutes=int(poll_interval))


def get_price_position(last_price, old_last_price, is_first):
    if last_price < old_last_price and not is_first:
        return -1
    elif last_price > old_last_price and not is_first:
        return 1
    else:
        return 0


def calc_price_and_percent(price, percent):
    return price * (1 + percent / 100)


def log_file(data):
    filename = os.path.join(BASE_DIR, 'tinkoff_invest.log')
    with open(filename, 'a+', encoding='utf-8') as log:
        log.write(str(datetime.now()) + "; " + str(data) + "\n")


class Instrument:
    def __init__(self):
        self.name = ''
        self.figi = ''
        self.currency = ''
        self.isin = ''
        self.lot = 0
        self.ticker = ''
        self.type = ''


class TinkoffInvest:
    def __init__(self):
        self.broker_account_id = ''
        self.is_sell_need = False
        self.client = None
        self.config = ConfigParser()
        dotenv_path = os.path.join(os.path.dirname(__file__), '.env')
        if os.path.exists(dotenv_path):
            load_dotenv(dotenv_path)
        config_path = BASE_DIR + os.path.normpath('/tinkoff_invest.ini')
        if not os.path.exists(config_path):
            self.create_config_section(config_path, 'general')
        self.config.read(config_path)
        self.instrument = Instrument()
        self.use_sandbox = self.get_config_section_value('general', 'use_sandbox', '1')
        self.token = os.getenv('token_sandbox')
        if self.use_sandbox == '0':
            self.token = os.getenv('token_production')

        self.last_buy_price = 0
        self.last_sell_price = 0
        self.figi = self.get_config_section_value('general', 'figi', 'BBG000B9XRY4')
        self.money_limit = self.get_config_section_value('general', 'money_limit', '500')
        poll_interval = self.get_config_section_value('general', 'poll_interval', '1')
        self.poll_interval = get_poll_interval(poll_interval)
        candle_config = self.get_config_section_value('general', 'candle_interval', '1min')
        self.candle_interval = get_candle_interval(candle_config)
        self.fee = self.get_config_section_value('general', 'fee', '0.025')
        self.trigger_buy_candle = self.get_config_section_value('general', 'trigger_buy_candle', '0.15')
        self.trigger_sell_candle = self.get_config_section_value('general', 'trigger_sell_candle', '0.15')
        self.trigger_buy_last_deal = self.get_config_section_value('general', 'trigger_buy_last_deal', '2')
        self.trigger_sell_last_deal = self.get_config_section_value('general', 'trigger_sell_last_deal', '2')

    def open(self):
        if self.use_sandbox == '1':
            self.client = tinvest.SyncClient(self.token, use_sandbox=True)
            body = SandboxRegisterRequest.tinkoff()
            response = self.client.register_sandbox_account(body)
            self.broker_account_id = response.payload.broker_account_id
        else:
            self.client = tinvest.SyncClient(self.token)
            log_file('Клиент подключен')

    def close(self):
        if self.use_sandbox == '1':
            response = self.client.clear_sandbox_account(self.broker_account_id)
            print(response.payload)

            # response = client.remove_sandbox_account(broker_account_id)
            # print(response.payload)

    def main(self):
        self.get_instrument_by_figi()
        while True:
            orderbook = self.get_orderbook()
            if orderbook.payload.trade_status == 'NormalTrading':
                hour_price = self.get_price_from_hour_candles()  # TODO: цена за последний час по свечке
                position_lots = self.search_position_lots_by_figi()  # TODO: позиция по figi
                last_price = orderbook.payload.last_price  # TODO: актуальная цена по стакану
                log_file(f'Актуальная цена {self.instrument.name}: {last_price}')
                if hour_price == 0:
                    hour_price = last_price
                if position_lots > 0:
                    price_last_buy = self.get_price_last_buy()
                    price_sell_last_candle = hour_price * (1 - Decimal(self.trigger_sell_candle) / Decimal(100))
                    price_sell_last_deal = price_last_buy * (
                            1 - Decimal(self.trigger_sell_last_deal) / Decimal(100))
                    # price_sell = max(price_sell_last_candle, price_sell_last_deal)
                    # if last_price < price_sell:
                    if self.is_price_sell_last_candle(hour_price, last_price, price_sell_last_candle) or \
                            self.is_price_sell_last_deal(price_last_buy, last_price, price_sell_last_deal):
                        # lots = self.get_lots_by_price(last_price)
                        log_file(f'Продажа {position_lots} лотов {self.instrument.name}')
                        # self.sell_market_order(self.figi, lots)
                else:
                    price_last_sell = self.get_price_last_sell()
                    if price_last_sell == 0:
                        price_last_sell = last_price
                    price_buy_last_candle = \
                        hour_price * (1 + Decimal(self.trigger_buy_candle) / Decimal(100))
                    price_buy_last_deal = price_last_sell * (
                            1 + Decimal(self.trigger_buy_last_deal) / Decimal(100))
                    # price_buy = min(price_buy_last_candle, price_buy_last_deal)
                    # if last_price > price_buy:
                    if self.is_price_buy_last_candle(hour_price, last_price, price_buy_last_candle) or \
                            self.is_price_buy_last_deal(price_last_sell, last_price, price_buy_last_deal):
                        lots = self.get_lots_by_price(last_price)
                        log_file(f'Покупка {lots} лотов {self.instrument.name}')
                        # self.buy_market_order(self.figi, lots)
            time.sleep(60)

    def is_price_sell_last_candle(self, hour_price, last_price, price_sell_last_candle):
        if last_price < price_sell_last_candle:
            log_file(
                f'Триггер часовой свечки для продажи. Цена открытия свечки: {hour_price}. Текущая цена: {last_price}. '
                f'Пороговая цена: {price_sell_last_candle}. Цена уменьшилась на {self.trigger_sell_candle}%')
            return True
        else:
            return False

    def is_price_sell_last_deal(self, price_last_buy, last_price, price_sell_last_deal):
        if last_price < price_sell_last_deal:
            log_file(
                f'Триггер с момента последней покупки. Цена последней покупки: {price_last_buy}. Текущая цена: {last_price}'
                f'Пороговая цена: {price_sell_last_deal}. Цена уменьшилась на {self.trigger_sell_last_deal}%')
            return True
        else:
            return False

    def is_price_buy_last_candle(self, hour_price, last_price, price_buy_last_candle):
        if last_price > price_buy_last_candle:
            log_file(
                f'Триггер часовой свечки для покупки. Цена открытия свечки: {hour_price}. Текущая цена: {last_price}. '
                f'Пороговая цена: {price_buy_last_candle}. Цена увеличилась на {self.trigger_buy_candle}%')
            return True
        else:
            return False

    def is_price_buy_last_deal(self, price_last_sell, last_price, price_buy_last_deal):
        if last_price > price_buy_last_deal:
            log_file(
                f'Триггер с момент последней продажи. Цена последней продажи: {price_last_sell}. Ткущая цена: {last_price}. '
                f'Пороговая цена: {price_buy_last_deal}. Цена увеличилась на {self.trigger_buy_last_deal}%')
            return True
        else:
            return False

    def get_lots_by_price(self, last_price):
        price = calc_price_and_percent(last_price, Decimal(self.fee))
        if Decimal(price) > Decimal(self.money_limit):
            return 0
        else:
            return Decimal(self.money_limit) // Decimal(price)

    def create_config_section(self, path, section_name):
        self.config.add_section(section_name)

        with open(path, "w") as config_file:
            self.config.write(config_file)

    def get_config_section_value(self, section_name, param_name, default_value):
        config_section = self.config[section_name]
        if param_name in config_section:
            return config_section[param_name]
        else:
            return default_value

    def set_sandbox_currency_balance(self, currency, balance):
        body = SandboxSetCurrencyBalanceRequest(
            balance=balance,
            currency=currency,
        )
        response = self.client.set_sandbox_currencies_balance(body, self.broker_account_id)
        print(response.payload)

    def set_sandbox_position_balance(self, figi, balance):
        body = SandboxSetPositionBalanceRequest(
            balance=balance,
            figi=figi,
        )
        response = self.client.set_sandbox_positions_balance(body, self.broker_account_id)
        print(response.payload)

    def search_position_lots_by_figi(self):
        log_file('Ищем позиции в портфолио')
        response = self.client.get_portfolio(self.broker_account_id)
        print(response.payload)
        status = response.status
        if status == 'Ok':
            for position in response.payload.positions:
                if self.figi == position.figi:
                    log_file(f'Портфолио: {position.name}. Лотов в позиции портфолио: {position.lots}')
                    return position.lots
        return 0

    def buy_market_order(self, figi, lots):
        log_file(f'Покупаем {lots} лотов {self.instrument.name}')
        body = MarketOrderRequest(
            lots=lots,
            operation='Buy'
        )
        response = self.client.post_orders_market_order(figi, body, self.broker_account_id)
        print(response.payload)

    def sell_market_order(self, figi, lots):
        log_file(f'Продаем {lots} лотов {self.instrument.name}')
        body = MarketOrderRequest(
            lots=lots,
            operation='Sell'
        )
        response = self.client.post_orders_market_order(figi, body, self.broker_account_id)
        print(response.payload)

    def get_orderbook(self):
        return self.client.get_market_orderbook(self.figi, 1)

    def get_last_price_from_orderbook(self):
        response = self.client.get_market_orderbook(self.figi, 1)
        return response.payload.last_price

    def get_price_from_hour_candles(self):
        log_file(f'Получаем цену из часовой свечки для {self.instrument.name}')
        delta = timedelta(hours=2)
        offset = timedelta(hours=0)
        tz = timezone(offset, name='London')
        begin = datetime.now(tz=tz) - delta
        end = datetime.now(tz=tz)

        response = self.client.get_market_candles(self.figi, begin, end, CandleResolution.hour)
        print(response.payload)
        if len(response.payload.candles) > 0:
            candle = response.payload.candles[-1]
            log_file(f'Цена из часовой свечки для {self.instrument.name}: {candle.o}')
            return candle.o
        else:
            delta = timedelta(weeks=2)
            offset = timedelta(hours=0)
            tz = timezone(offset, name='London')
            begin = datetime.now(tz=tz) - delta
            end = datetime.now(tz=tz)

            response = self.client.get_market_candles(self.figi, begin, end, CandleResolution.hour)
            if len(response.payload.candles) > 0:
                candle = response.payload.candles[-1]
                log_file(f'Цена из часовой свечки для {self.instrument.name}: {candle.o}')
                return candle.o
            else:
                return 0

    def get_price_last_buy(self):
        log_file('Получаем цену последней покупки из портфолио')
        response = self.client.get_portfolio(self.broker_account_id)
        status = response.status
        if status == 'Ok':
            for position in response.payload.positions:
                if self.figi == position.figi:
                    log_file(
                        f'Портфолио: {position.name}. Цена последней покупки: {position.average_position_price.value}')
                    return position.average_position_price.value

    def get_price_last_sell(self):
        log_file('Получаем цену последней продажи из истории операций')
        delta = timedelta(weeks=4)
        offset = timedelta(hours=0)
        tz = timezone(offset, name='London')
        begin = datetime.now(tz=tz) - delta
        end = datetime.now(tz=tz)
        response = self.client.get_operations(begin, end, self.figi, self.broker_account_id)
        print(response.payload)
        for operation in response.payload.operations:
            if operation.operation_type == 'Buy':
                log_file(f'Цена последней продажи для {self.instrument.name}: {operation.price}')
                return operation.price
        return 0

    def get_instrument_by_figi(self):
        response = self.client.get_market_search_by_figi(self.figi)
        print(response.payload)
        payload = response.payload
        self.instrument.name = payload.name
        self.instrument.figi = payload.figi
        self.instrument.isin = payload.isin
        self.instrument.ticker = payload.ticker
        self.instrument.currency = payload.currency
        self.instrument.type = payload.type


if __name__ == "__main__":
    tinkoff = TinkoffInvest()
    tinkoff.open()
    tinkoff.main()
