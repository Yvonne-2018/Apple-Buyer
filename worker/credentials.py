"""Use the OS vault explicitly; never fall back to a plaintext keyring backend."""
import sys


class Vault:
    service = 'com.niulai.desktop'

    def __init__(self):
        self.backend = None
        if sys.platform == 'darwin':
            from keyring.backends.macOS import Keyring
            self.backend = Keyring()
        elif sys.platform == 'win32':
            from keyring.backends.Windows import WinVaultKeyring
            self.backend = WinVaultKeyring()

    def get(self, account_id):
        return self.backend.get_password(self.service, account_id) if self.backend else None

    def save(self, account_id, password):
        if not self.backend:
            raise ValueError('当前系统没有可用的凭据保险库。')
        self.backend.set_password(self.service, account_id, password)

    def delete(self, account_id):
        if self.backend:
            from keyring.errors import PasswordDeleteError
            try:
                self.backend.delete_password(self.service, account_id)
            except PasswordDeleteError:
                # Missing credentials are already cleared; real deletion failures must surface.
                if self.get(account_id) is not None:
                    raise


class LocalVault:
    """Explicit local-file storage, not encrypted; never consult the OS vault."""
    def __init__(self, account_dir):
        from pathlib import Path
        self.path = Path(account_dir) / 'local-password.json'

    def get(self, account_id):
        import json
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding='utf-8'))
        if data.get('account_id') != account_id or not isinstance(data.get('password'), str):
            raise ValueError('本地密码文件不属于当前账号或格式错误，请重新设置。')
        return data['password'] or None

    def save(self, account_id, password):
        from duo_killer import write_private_json
        if not isinstance(password, str) or not password or len(password) > 4096:
            raise ValueError('请输入有效密码。')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_private_json(self.path, {'account_id': account_id, 'password': password})

    def delete(self, account_id):
        self.path.unlink(missing_ok=True)
