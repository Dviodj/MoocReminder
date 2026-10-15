"""Per-account profiles, so each MOOC account keeps its own settings and data.

Layout under the data folder:

    app.json                 which account is active, plus settings shared by all
    accounts/<key>/data.json one profile: its own MOOC home, email, cache, marks

A profile owns everything that belongs to one account: the MOOC home link, the
email binding, the cached assignments, completion marks and reminder history.
The shared file keeps the settings that make no sense per account (check
interval, reminder hours, what to include).
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile

from core import DEFAULT_SETTINGS, account_id, binding_state as core_binding_state, is_bound, valid_home_url
from storage import Store, default_data_dir, normalize_email_settings

APP_FILE = 'app.json'
ACCOUNTS_DIR = 'accounts'
# Settings that describe how the program behaves, not who it is.
GLOBAL_KEYS = ('interval_minutes', 'reminder_hours', 'include_quizzes', 'include_finished',
               'autostart_prompted')
FORMAT = 2


def split_settings(settings):
    """Return (shared, per-account) halves of a settings mapping."""
    shared = {key: deepcopy(settings[key]) for key in GLOBAL_KEYS if key in settings}
    return shared, deepcopy(settings)


def merge_settings(shared, profile):
    """Effective settings: shared defaults with this account's own values."""
    return {**deepcopy(DEFAULT_SETTINGS), **deepcopy(shared or {}), **deepcopy(profile or {})}


def slugify(text):
    """Folder-safe version of a user-visible account name."""
    slug = re.sub(r'[^\w\u4e00-\u9fff-]+', '-', str(text or '').strip()).strip('-')
    return slug[:40] or 'account'


def profile_key(home_url, name=''):
    """Folder name for a profile: the MOOC user id when known, else the name."""
    user = account_id(home_url)
    if user.isdigit() and user != '0':
        return user
    return slugify(name) if str(name or '').strip() else 'default'


def account_label(settings):
    """Human label for an account: its name, else the MOOC id, else a default."""
    name = str((settings or {}).get('name', '') or '').strip()
    if name:
        return name
    user = account_id((settings or {}).get('home_url', ''))
    if user.isdigit() and user != '0':
        return '慕课账号 ' + user
    return '默认账号'


def binding_state(settings):
    """Account bindings; `core` owns the rule, this keeps imports local."""
    return core_binding_state(settings)


class Profiles:
    """Owns the account list and decides which profile the program works on."""

    def __init__(self, folder=None):
        self.folder = Path(folder) if folder else default_data_dir()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.app_path = self.folder/APP_FILE
        self.accounts_dir = self.folder/ACCOUNTS_DIR
        self.shared = {key: deepcopy(DEFAULT_SETTINGS[key]) for key in GLOBAL_KEYS}
        self.active = self._load_app()
        if self.active is None:
            self.active = self._adopt_legacy()
        if self.active is None:
            # Nothing to inherit from yet, so start from the defaults. This
            # placeholder is replaced by the first real account rather than
            # lingering as an empty entry.
            self.active = self.create_profile(activate=True, key='default',
                                              settings={**deepcopy(DEFAULT_SETTINGS), 'placeholder': True})
        else:
            self.set_active(self.active)

    # ---- app-level state -------------------------------------------------

    def _load_app(self):
        try:
            saved = json.loads(self.app_path.read_text(encoding='utf-8'))
            if not isinstance(saved, dict):
                return None
            shared = saved.get('shared')
            if isinstance(shared, dict):
                for key in GLOBAL_KEYS:
                    if key in shared:
                        self.shared[key] = shared[key]
            active = str(saved.get('active', '') or '')
            return active if active and self.profile_folder(active).is_dir() else None
        except (OSError, ValueError):
            return None

    def _save_app(self):
        payload = dict(format=FORMAT, active=self.active, shared=deepcopy(self.shared))
        temporary = self.app_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, self.app_path)
        return payload

    def _adopt_legacy(self):
        """Move a pre-profile data.json into the account layout, losing nothing."""
        legacy = self.folder/'data.json'
        if not legacy.is_file():
            return None
        try:
            saved = json.loads(legacy.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if not isinstance(saved, dict):
            return None
        settings = saved.get('settings') if isinstance(saved.get('settings'), dict) else {}
        home = settings.get('home_url', '')
        key = profile_key(home if valid_home_url(home) else '', settings.get('name', ''))
        target = self.profile_folder(key)
        target.mkdir(parents=True, exist_ok=True)
        if not (target/'data.json').exists():
            (target/'data.json').write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
        legacy.rename(self.folder/'data.migrated.json')
        for name in GLOBAL_KEYS:
            if name in settings:
                self.shared[name] = settings[name]
        self.active = key
        self._save_app()
        return key

    # ---- accounts --------------------------------------------------------

    def profile_folder(self, key):
        return self.accounts_dir/str(key)

    def profile_exists(self, key):
        return (self.profile_folder(key)/'data.json').is_file()

    def list_profiles(self):
        found = []
        if self.accounts_dir.is_dir():
            for entry in sorted(self.accounts_dir.iterdir()):
                if entry.is_dir() and (entry/'data.json').is_file():
                    try:
                        saved = json.loads((entry/'data.json').read_text(encoding='utf-8'))
                        settings = saved.get('settings', {}) if isinstance(saved, dict) else {}
                    except (OSError, ValueError):
                        settings = {}
                    found.append(dict(key=entry.name, label=account_label(settings),
                                      home_url=str(settings.get('home_url', '')),
                                      email=str((settings.get('email') or {}).get('recipient', '')),
                                      active=entry.name == self.active))
        return found

    def current_account(self):
        """The profile the program is working on."""
        home = self.load_store().data['settings'].get('home_url', '')
        for entry in self.list_profiles():
            if entry['key'] == self.active:
                return entry
        for entry in self.list_profiles():
            if entry['home_url'] == home:
                return entry
        return None

    def login(self, key):
        """Enter an account; no password is involved, the account is local."""
        return self.set_active(key)

    def take_placeholder(self):
        """Claim the untouched placeholder, so it is not listed as a real account."""
        key = self._placeholder_key()
        if not key:
            return None
        return key

    def account_options(self):
        """The real accounts a signed-in chooser should offer, without the placeholder."""
        placeholder = self._placeholder_key()
        return [entry for entry in self.list_profiles() if entry['key'] != placeholder]
    def unique_account_name(self, name):
        """Keep account names distinct; the name is the user's only handle."""
        wanted = str(name or '').strip()
        if not wanted:
            raise ValueError('请填写账号名称。')
        existing = {entry['label'].casefold() for entry in self.list_profiles()}
        candidate, index = wanted, 2
        while candidate.casefold() in existing:
            candidate = f'{wanted} {index}'
            index += 1
        return candidate

    def register(self, name, settings=None):
        """Create an account from just a name; MOOC and email are bound after entering.

        The new account starts from the one being used, so an already configured
        email binding does not have to be typed in again.
        """
        wanted = self.unique_account_name(name)
        if settings is None:
            # Read the source now: create_profile may take over (rename) the
            # placeholder, so it cannot be looked up once that has happened.
            settings = self._inheritance_source(None, self._placeholder_key())
        return self.create_profile(name=wanted, settings=settings, activate=True)

    def load_store(self, key=None):
        """Store for a profile, with shared settings applied on top."""
        key = key or self.active
        store = Store(self.profile_folder(key), settings_base=self.shared)
        return store

    def set_active(self, key):
        if not self.profile_exists(key):
            raise ValueError('没有找到这个账号，请重新选择。')
        self.active = str(key)
        self._save_app()
        return self.active

    def save_shared(self, settings):
        for name in GLOBAL_KEYS:
            if name in settings:
                self.shared[name] = deepcopy(settings[name])
        self._save_app()

    def _placeholder_key(self):
        """The placeholder profile waiting for a real account, if any.

        A registered account may be bound to nothing yet, so "has no email" is
        not enough to recognise the placeholder; the explicit marker decides.
        """
        for entry in self.list_profiles():
            try:
                saved = json.loads((self.profile_folder(entry['key'])/'data.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if (saved.get('settings') or {}).get('placeholder'):
                return entry['key']
        return None

    def _rename_profile(self, current, wanted):
        """Give a reused placeholder the folder name this account deserves."""
        if current == wanted or self.profile_folder(wanted).exists():
            return current
        try:
            self.profile_folder(current).rename(self.profile_folder(wanted))
        except OSError:
            return current
        if self.active == current:
            self.active = wanted
        return wanted

    def _inheritance_source(self, inherit_from, placeholder):
        """Settings a new account starts from.

        `inherit_from` is an explicit choice and must exist; without it the
        account in use decides, and the placeholder is allowed to be that
        source even though it is not a real account.
        """
        if inherit_from:
            if not self.profile_exists(inherit_from):
                raise ValueError('要沿用的账号不存在，请重新选择。')
            return self.load_store(inherit_from).data['settings']
        origin = self.active if self.profile_exists(self.active) else placeholder
        return self.load_store(origin).data['settings']

    def create_profile(self, name='', home_url='', settings=None, inherit_from=None,
                       activate=False, key=None):
        """Add an account; by default it inherits the email binding and shared settings."""
        if home_url and not valid_home_url(home_url):
            raise ValueError('课程主页链接需要包含你的 userId。')
        # Work out the name this account deserves first: `or` would otherwise
        # short-circuit on the placeholder and keep its folder name.
        wanted = key or profile_key(home_url, name)
        # A caller that asks for a real account (a name or a course page) takes
        # over the placeholder; one that passes settings supplies its own.
        wants_real_account = bool(str(name or '').strip() or str(home_url or '').strip())
        placeholder = self._placeholder_key() if wants_real_account else None
        reused = self._rename_profile(placeholder, wanted) if placeholder else None
        if reused:
            # The placeholder already holds the folder this account wants.
            attempt = reused
        else:
            attempt, suffix = wanted, 2
            while self.profile_exists(attempt) or self.profile_folder(attempt).exists():
                attempt = f'{wanted}-{suffix}'
                suffix += 1
        target = self.profile_folder(attempt)
        target.mkdir(parents=True, exist_ok=True)
        source = settings
        if source is None:
            source = self._inheritance_source(inherit_from, placeholder)
        template = {**deepcopy(source), 'name': str(name or '').strip(),
                    'home_url': home_url or DEFAULT_SETTINGS['home_url']}
        if home_url:
            template['manual_urls'] = []
        merged = merge_settings(self.shared, template)
        if reused:
            # The marker has done its job; this is a real account now.
            merged.pop('placeholder', None)
        merged['email'] = normalize_email_settings({'email': merged.get('email') or {}})
        store = Store(target, settings_base=self.shared)
        store.data['settings'] = merged
        store.save()
        self._save_app()
        if activate:
            self.active = attempt
            self._save_app()
        return attempt

    def forget_profile(self, key):
        """Remove an account's profile after keeping a backup copy."""
        folder = self.profile_folder(key)
        if not (folder/'data.json').is_file():
            raise ValueError('没有找到这个账号。')
        if len(self.list_profiles()) <= 1:
            raise ValueError('至少保留一个账号。')
        backup = self.folder/('removed-'+str(key)+'.json')
        backup.write_bytes((folder/'data.json').read_bytes())
        for path in sorted(folder.rglob('*'), reverse=True):
            try:
                path.unlink() if path.is_file() else path.rmdir()
            except OSError:
                pass
        try:
            folder.rmdir()
        except OSError:
            pass
        if self.active == key:
            remaining = self.list_profiles()
            if remaining:
                self.active = remaining[0]['key']
        self._save_app()
        return backup


def folder_for_store(store_folder):
    """Data folder that owns a profile, whether or not profiles are in use yet."""
    folder = Path(store_folder).resolve()
    if folder.name and folder.parent.name == ACCOUNTS_DIR:
        return folder.parent.parent
    return folder


def open_profiles(folder=None):
    """Entry point used by the launcher: profiles plus the active profile store."""
    profiles = Profiles(folder)
    return profiles, profiles.load_store()
