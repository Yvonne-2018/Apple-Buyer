import io
import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from credentials import LocalVault
from protocol import Channel
from service import Service
import interaction
import duo_killer as d
import alipay_qr


class LocalPasswordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        saved = {k:getattr(d,k) for k in ('ROOT','AUTH_PATH','LOG_PATH','PAYMENT_ROOT')}
        old_handler, old_qr = interaction.handler, alipay_qr.ROOT
        def restore():
            for k,v in saved.items():setattr(d,k,v)
            interaction.handler=old_handler;alipay_qr.ROOT=old_qr
        self.addCleanup(restore)

    def test_local_roundtrip_private_permissions_account_isolation_and_delete(self):
        vault = LocalVault(self.root)
        with patch('credentials.Vault', side_effect=AssertionError('OS vault must not be used')):
            self.assertIsNone(vault.get('a'))
            vault.save('a','local-test-secret')
            self.assertEqual(vault.get('a'),'local-test-secret')
            if os.name != 'nt':self.assertEqual(vault.path.stat().st_mode & 0o777,0o600)
            with self.assertRaises(ValueError):vault.get('b')
            vault.delete('a');self.assertIsNone(vault.get('a'))

    def test_service_uses_local_password_without_prompt_or_keychain(self):
        LocalVault(self.root).save('a','local-test-secret')
        output = io.StringIO();channel = Channel('a','t',output)
        with patch('credentials.Vault', side_effect=AssertionError('no keychain')), patch.object(channel,'prompt',side_effect=AssertionError('no prompt')):
            service=Service(channel,self.root,self.root,'test@example.invalid')
            self.assertEqual(service.credentials(None)[1],'local-test-secret')
        self.assertNotIn('local-test-secret', output.getvalue())

    def test_settings_save_and_clear_never_returns_secret(self):
        channel=Channel('a','t',io.StringIO());service=Service(channel,self.root,self.root,'test@example.invalid')
        with patch.object(channel,'prompt',return_value={'password':'local-test-secret','remember':True}):
            result=service.run('password_save',{})
        self.assertTrue(result['success']);self.assertNotIn('local-test-secret',json.dumps(result))
        self.assertEqual(LocalVault(self.root).get('a'),'local-test-secret')
        service.run('forget_password',{});self.assertIsNone(LocalVault(self.root).get('a'))

    def test_unchecked_save_does_not_write(self):
        channel=Channel('a','t',io.StringIO());service=Service(channel,self.root,self.root,'test@example.invalid')
        with patch.object(channel,'prompt',return_value={'password':'local-test-secret','remember':False}):
            service.run('password_save',{})
        self.assertFalse(LocalVault(self.root).path.exists())
