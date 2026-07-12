import os
import subprocess
import sys
from contextlib import contextmanager
from unittest.mock import patch

from base import BaseTest

from seedsigner.gui.components import Fonts, GUIConstants
from seedsigner.models.l10n_assets import L10nAssets
from seedsigner.models.settings_definition import SettingsConstants


def create_mo_file(l10n_dir: str, locale: str, size: int = 64) -> str:
    mo_dir = os.path.join(l10n_dir, locale, "LC_MESSAGES")
    os.makedirs(mo_dir, exist_ok=True)
    mo_file = os.path.join(mo_dir, "messages.mo")
    with open(mo_file, "wb") as f:
        f.write(b"\x00" * size)
    return mo_file


def create_font_file(fonts_dir: str, font_name: str, size: int = 64) -> str:
    os.makedirs(fonts_dir, exist_ok=True)
    font_file = os.path.join(fonts_dir, f"{font_name}.ttf")
    with open(font_file, "wb") as f:
        f.write(b"\x00" * size)
    return font_file


@contextmanager
def sandboxed_asset_dirs(tmp_path, bundled_locales: list[str] = None):
    """
    Redirect all L10nAssets dirs into tmp_path. With no `bundled_locales`,
    L10nAssets runs in external mode.
    """
    bundled_dir = str(tmp_path / "bundled" / "l10n")
    for locale in bundled_locales or []:
        create_mo_file(bundled_dir, locale)

    with patch.object(L10nAssets, "bundled_l10n_dir", return_value=bundled_dir), \
            patch.object(L10nAssets, "EXTERNAL_L10N_DIR", str(tmp_path / "microsd" / "l10n")), \
            patch.object(L10nAssets, "CACHE_DIR", str(tmp_path / "tmp" / "l10n")), \
            patch.object(L10nAssets, "CACHE_FONTS_DIR", str(tmp_path / "tmp" / "l10n" / "fonts")):
        yield


class TestL10nAssets(BaseTest):
    def test_import_settings_in_external_mode(self):
        """
        Regression test: importing the settings module with no bundled translations
        (i.e. on a SeedSigner OS build) must not raise. settings_definition builds
        its Language options at import time, which pulls in L10nAssets; a top-level
        import back at settings_definition from l10n_assets is circular and only
        blows up in external mode (device crash-loop at boot).
        """
        script = (
            "from unittest.mock import patch\n"
            # Force get_detected_languages' bundled-assets scan to come up empty,
            # exactly like a rootfs whose translations were stripped at build time
            "patch('os.walk', return_value=[]).start()\n"
            "import seedsigner.models.settings\n"
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        assert result.returncode == 0, f"import failed in external mode:\n{result.stderr}"


    def test_bundled_mode(self, tmp_path):
        """ With bundled translations present, external-asset logic should no-op """
        with sandboxed_asset_dirs(tmp_path, bundled_locales=["es"]):
            assert L10nAssets.has_bundled_assets() is True
            assert L10nAssets.gettext_localedir() == L10nAssets.bundled_l10n_dir()

            # refresh_cache is a no-op success; nothing lands in the cache dir
            assert L10nAssets.refresh_cache(SettingsConstants.LOCALE__SPANISH) is True
            assert not os.path.exists(L10nAssets.CACHE_DIR)


    def test_external_mode(self, tmp_path):
        """ Without bundled translations, gettext should read from the tmpfs cache """
        with sandboxed_asset_dirs(tmp_path):
            assert L10nAssets.has_bundled_assets() is False
            assert L10nAssets.gettext_localedir() == L10nAssets.CACHE_DIR


    def test_refresh_cache__english_needs_no_assets(self, tmp_path):
        with sandboxed_asset_dirs(tmp_path):
            assert L10nAssets.refresh_cache(SettingsConstants.LOCALE__ENGLISH) is True
            assert not os.path.exists(L10nAssets.CACHE_DIR)


    def test_refresh_cache__copies_mo_from_card(self, tmp_path):
        with sandboxed_asset_dirs(tmp_path):
            create_mo_file(L10nAssets.EXTERNAL_L10N_DIR, SettingsConstants.LOCALE__SPANISH)

            assert L10nAssets.refresh_cache(SettingsConstants.LOCALE__SPANISH) is True
            assert os.path.exists(os.path.join(L10nAssets.CACHE_DIR, SettingsConstants.LOCALE__SPANISH, "LC_MESSAGES", "messages.mo"))


    def test_refresh_cache__copies_locale_font(self, tmp_path):
        locale = SettingsConstants.LOCALE__KOREAN
        font_name = GUIConstants.BASE_LOCALE_FONTS[locale]
        with sandboxed_asset_dirs(tmp_path):
            create_mo_file(L10nAssets.EXTERNAL_L10N_DIR, locale)
            create_font_file(os.path.join(L10nAssets.EXTERNAL_L10N_DIR, "fonts"), font_name)

            # Seed a stale (fallback) entry that must be evicted once the real font
            # is cached
            Fonts.fonts[font_name] = {17: "stale fallback font"}

            assert L10nAssets.refresh_cache(locale) is True
            assert os.path.exists(os.path.join(L10nAssets.CACHE_FONTS_DIR, f"{font_name}.ttf"))
            assert font_name not in Fonts.fonts


    def test_refresh_cache__card_not_available(self, tmp_path):
        """ Mimics booting with the card out or yanking it before a locale switch """
        with sandboxed_asset_dirs(tmp_path):
            assert L10nAssets.refresh_cache(SettingsConstants.LOCALE__SPANISH) is False


    def test_refresh_cache__missing_font_fails(self, tmp_path):
        """ A locale that requires a font isn't usable if the font can't be cached """
        locale = SettingsConstants.LOCALE__KOREAN
        with sandboxed_asset_dirs(tmp_path):
            create_mo_file(L10nAssets.EXTERNAL_L10N_DIR, locale)
            assert L10nAssets.refresh_cache(locale) is False


    def test_refresh_cache__failure_preserves_previous_cache(self, tmp_path):
        locale = SettingsConstants.LOCALE__SPANISH
        with sandboxed_asset_dirs(tmp_path):
            create_mo_file(L10nAssets.EXTERNAL_L10N_DIR, locale)
            assert L10nAssets.refresh_cache(locale) is True

            # Card removed: refresh fails but the cached copy is untouched
            cached_mo = os.path.join(L10nAssets.CACHE_DIR, locale, "LC_MESSAGES", "messages.mo")
            with patch.object(L10nAssets, "EXTERNAL_L10N_DIR", str(tmp_path / "nonexistent")):
                assert L10nAssets.refresh_cache(locale) is False
            assert os.path.exists(cached_mo)


    def test_refresh_cache__rejects_oversized_files(self, tmp_path):
        """ The cache is tmpfs (RAM); implausibly large files must be refused """
        locale = SettingsConstants.LOCALE__SPANISH
        with sandboxed_asset_dirs(tmp_path), \
                patch.object(L10nAssets, "MAX_ASSET_FILE_SIZE", 32):
            create_mo_file(L10nAssets.EXTERNAL_L10N_DIR, locale, size=64)
            assert L10nAssets.refresh_cache(locale) is False
            assert not os.path.exists(os.path.join(L10nAssets.CACHE_DIR, locale, "LC_MESSAGES", "messages.mo"))


    def test_get_external_locales(self, tmp_path):
        """ Should union the card's locales with the already-cached ones """
        with sandboxed_asset_dirs(tmp_path):
            create_mo_file(L10nAssets.EXTERNAL_L10N_DIR, SettingsConstants.LOCALE__SPANISH)
            create_mo_file(L10nAssets.EXTERNAL_L10N_DIR, SettingsConstants.LOCALE__FRENCH)
            create_mo_file(L10nAssets.CACHE_DIR, SettingsConstants.LOCALE__KOREAN)

            assert L10nAssets.get_external_locales() == {
                SettingsConstants.LOCALE__SPANISH,
                SettingsConstants.LOCALE__FRENCH,
                SettingsConstants.LOCALE__KOREAN,
            }


    def test_get_detected_languages__external_mode(self, tmp_path):
        """
        With no bundled translations, get_detected_languages should offer English
        plus the external locales.
        """
        with sandboxed_asset_dirs(tmp_path):
            create_mo_file(L10nAssets.EXTERNAL_L10N_DIR, SettingsConstants.LOCALE__SPANISH)

            # Make the bundled-assets os.walk scan come up empty
            with patch("os.walk", return_value=[]):
                detected = [lang for lang, display_name in SettingsConstants.get_detected_languages()]

            assert detected == [SettingsConstants.LOCALE__ENGLISH, SettingsConstants.LOCALE__SPANISH]
