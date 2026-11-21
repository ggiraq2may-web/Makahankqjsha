"""Offline unit tests. Telegram classes are stubbed only if SDK unavailable."""
import asyncio
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import httpx

try:
    import telegram  # noqa: F401
except ImportError:
    import sys
    from types import ModuleType, SimpleNamespace
    telegram_stub = ModuleType('telegram')
    telegram_ext_stub = ModuleType('telegram.ext')

    class InlineKeyboardButton:
        def __init__(self, text, callback_data=None, url=None):
            self.text = text
            self.callback_data = callback_data
            self.url = url

    class InlineKeyboardMarkup:
        def __init__(self, inline_keyboard):
            self.inline_keyboard = inline_keyboard

    telegram_stub.InlineKeyboardButton = InlineKeyboardButton
    telegram_stub.InlineKeyboardMarkup = InlineKeyboardMarkup
    telegram_stub.Update = type('Update', (), {})
    for name in ('Application', 'CallbackQueryHandler', 'CommandHandler', 'MessageHandler'):
        setattr(telegram_ext_stub, name, type(name, (), {}))
    telegram_ext_stub.ContextTypes = SimpleNamespace(DEFAULT_TYPE=object)
    telegram_ext_stub.filters = SimpleNamespace(TEXT=True, COMMAND=False)
    sys.modules['telegram'] = telegram_stub
    sys.modules['telegram.ext'] = telegram_ext_stub

import main


class KroTests(unittest.TestCase):
    def test_player_id_validation(self):
        self.assertTrue(main.PLAYER_ID_RE.fullmatch('5128100402'))
        for value in ('abc', '1234', '1'*21, '12345;DROP TABLE'):
            self.assertIsNone(main.PLAYER_ID_RE.fullmatch(value))

    def test_official_shop_link(self):
        self.assertEqual(main.TURKEY_SHOP_URL, 'https://www.midasbuy.com/midasbuy/tr/buy/pubgm')
        buttons = [b for row in main.keyboard().inline_keyboard for b in row]
        self.assertEqual(len([b for b in buttons if b.url == main.TURKEY_SHOP_URL]), 1)
        self.assertIn('لا يغيّر منطقة الحساب', main.purchase_note())

    def test_backward_compatible_db_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'old.sqlite')
            with sqlite3.connect(path) as conn:
                conn.execute('CREATE TABLE operations (id INTEGER PRIMARY KEY AUTOINCREMENT, '
                             'user_id INTEGER NOT NULL, player_id TEXT NOT NULL, '
                             'target_region TEXT NOT NULL, operation_status TEXT NOT NULL, '
                             'verification_status TEXT NOT NULL, evidence_source TEXT, '
                             'created_at TEXT NOT NULL)')
                conn.execute('INSERT INTO operations (user_id, player_id, target_region, '
                             'operation_status, verification_status, created_at) VALUES '
                             "(1, '123456', 'Turkey', 'NOT_PERFORMED', 'UNVERIFIED', '2026')")
            with patch.object(main, 'DB_PATH', path):
                with main.connect() as conn:
                    cols = {r[1] for r in conn.execute('PRAGMA table_info(operations)')}
                    num = conn.execute('SELECT count(*) FROM operations').fetchone()[0]
                self.assertIn('lookup_id', cols)
                self.assertEqual(num, 1)

    def test_provider_config(self):
        with patch.dict(os.environ, {'PLAYER_LOOKUP_URL': 'https://example.org/players/{player_id}'}):
            self.assertIn('{player_id}', main.provider_config())
        with patch.dict(os.environ, {'PLAYER_LOOKUP_URL': 'http://localhost/{player_id}'}):
            with self.assertRaises(ValueError):
                main.provider_config()
        with patch.dict(os.environ, {'PLAYER_LOOKUP_URL': 'https://example.org/player'}):
            with self.assertRaises(ValueError):
                main.provider_config()

    def test_history_has_no_fake_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(main, 'DB_PATH', os.path.join(tmp, 'db.sqlite')):
                main.record(100, '5128100402', {'status': 'LOOKUP_SUCCESS', 'nickname': 'Test'})
                with sqlite3.connect(main.DB_PATH) as conn:
                    lookup = conn.execute('SELECT status FROM lookups').fetchone()[0]
                    operation = conn.execute('SELECT operation_status, verification_status, lookup_id '
                                             'FROM operations').fetchone()
                self.assertEqual(lookup, 'LOOKUP_SUCCESS')
                self.assertEqual(operation, ('NOT_PERFORMED', 'UNVERIFIED', 1))

    def test_provider_mock_and_rate_limit(self):
        def handler(request):
            if request.url.path.endswith('999999'):
                return httpx.Response(429)
            return httpx.Response(200, json={'info': {'charac_name': 'Sample', 'zone_id': '1'}})

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch.dict(os.environ, {'PLAYER_LOOKUP_URL': 'https://example.org/{player_id}'}):
                    ok = await main.lookup_player('5128100402', client=client)
                    limited = await main.lookup_player('999999', client=client)
            return ok, limited
        ok, limited = asyncio.run(run())
        self.assertEqual(ok['status'], 'LOOKUP_SUCCESS')
        self.assertIsNone(ok['player_region'])
        self.assertEqual(limited['status'], 'RATE_LIMITED')


if __name__ == '__main__':
    unittest.main()

class AdditionalProviderTests(unittest.TestCase):
    def test_unconfigured_provider(self):
        async def run():
            with patch.dict(os.environ, {'PLAYER_LOOKUP_URL':''}):
                return await main.lookup_player('5128100402')
        self.assertEqual(asyncio.run(run())['status'],'PROVIDER_NOT_CONFIGURED')

    def test_unauthorized_not_found_redirect_server_and_invalid(self):
        cases = [
            (401,None,'PROVIDER_UNAUTHORIZED'),
            (403,None,'PROVIDER_UNAUTHORIZED'),
            (404,None,'PLAYER_NOT_FOUND'),
            (429,None,'RATE_LIMITED'),
            (302,None,'PROVIDER_REDIRECT_REJECTED'),
            (500,None,'PROVIDER_FAILED'),
            (200,{'success':False},'PLAYER_NOT_FOUND'),
            (200,[], 'INVALID_RESPONSE'),
            (200,{'info':None},'INVALID_RESPONSE'),
            (200,{'info':{'zone_id':4}},'UNRECOGNIZED_RESPONSE'),
        ]
        async def run():
            results=[]
            for status,body,expected in cases:
                def handler(request,status=status,body=body):
                    return httpx.Response(status,json=body) if body is not None else httpx.Response(status)
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    with patch.dict(os.environ, {'PLAYER_LOOKUP_URL':'https://example.org/v1/{player_id}'}):
                        res=await main.lookup_player('5128100402',client=client)
                results.append((expected,res['status']))
            return results
        for expected,received in asyncio.run(run()):
            self.assertEqual(expected,received)

    def test_provider_timeout_exception(self):
        def handler(request):
            raise httpx.ReadTimeout('simulated',request=request)
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch.dict(os.environ, {'PLAYER_LOOKUP_URL':'https://example.org/v1/{player_id}'}):
                    return await main.lookup_player('5128100402',client=client)
        self.assertEqual(asyncio.run(run())['status'],'TIMEOUT')

    def test_provider_valid_name_and_region(self):
        def handler(request):
            return httpx.Response(200,json={'info':{'charac_name':'Sample','user_region':'AE'}})
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch.dict(os.environ, {'PLAYER_LOOKUP_URL':'https://example.org/v1/{player_id}'}):
                    return await main.lookup_player('5128100402',client=client)
        result=asyncio.run(run())
        self.assertEqual(result['player_region'],'AE')
        self.assertEqual(result['nickname'],'Sample')

    def test_mocked_telegram_user_flow_keeps_false_claims_off(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        class FakeSent:
            def __init__(self): self.edited=None
            async def edit_text(self,body,**kwargs): self.edited=body
        class FakeMsg:
            def __init__(self,txt): self.text=txt; self.sent=[]; self.message=FakeSent()
            async def reply_text(self,body,**kwargs):
                self.sent.append(body)
                return self.message
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(main,'DB_PATH',os.path.join(tmp,'bot.sqlite')):
                msg=FakeMsg('5128100402')
                update=SimpleNamespace(message=msg,effective_user=SimpleNamespace(id=555))
                context=SimpleNamespace(user_data={'awaiting':True})
                with patch.object(main,'lookup_player',new=AsyncMock(return_value={
                    'status':'LOOKUP_SUCCESS','nickname':'Example','player_region':'AE'})):
                    asyncio.run(main.receive(update,context))
                self.assertIn('تغيير المنطقة: لم يُنفّذ',msg.message.edited)
                self.assertIn('AE',msg.message.edited)
                with main.connect() as db:
                    rows=db.execute('SELECT operation_status, verification_status FROM operations').fetchall()
                self.assertEqual(rows,[('NOT_PERFORMED','UNVERIFIED')])

    def test_bad_id_cannot_enter_provider(self):
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        class Msg:
            text='123;DROP'
            def __init__(self):self.sent=[]
            async def reply_text(self,text,**kwargs):self.sent.append(text)
        m=Msg()
        update=SimpleNamespace(message=m,effective_user=SimpleNamespace(id=1))
        context=SimpleNamespace(user_data={'awaiting':True})
        with patch.object(main,'lookup_player',new=AsyncMock()) as provider:
            asyncio.run(main.receive(update,context))
            provider.assert_not_called()
        self.assertIn('غير صالح',m.sent[0])
