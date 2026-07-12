import glob
import logging
import os
import pathlib
import shutil

# NOTE: No top-level seedsigner imports allowed here. settings_definition builds its
# Language options at import time, which in external mode imports this module (see
# get_detected_languages); any import back at settings_definition would be circular.

logger = logging.getLogger(__name__)


class L10nAssets:
    """
    Locates localization assets: compiled gettext catalogs (messages.mo) and the
    locale-specific fonts.

    Two modes:

    * Bundled mode: the seedsigner-translations submodule is present in
      resources/seedsigner-translations (desktop dev, Raspberry Pi OS manual installs,
      test suites). All external-asset logic no-ops; behavior is identical to before
      this class existed.

    * External mode: SeedSigner OS builds strip the bundled assets out of the rootfs
      (to shrink the RAM-resident initramfs) and ship them on the FAT boot partition
      instead, under /mnt/microsd/l10n. The active locale's assets are copied into a
      tmpfs cache (/tmp/l10n) so the language keeps working after the microsd card is
      removed.
    """
    EXTERNAL_L10N_DIR = "/mnt/microsd/l10n"
    CACHE_DIR = "/tmp/l10n"
    CACHE_FONTS_DIR = "/tmp/l10n/fonts"

    # The cache lives in tmpfs (RAM); refuse implausibly large files from the SD card
    MAX_ASSET_FILE_SIZE = 5 * 1024 * 1024


    @classmethod
    def bundled_l10n_dir(cls) -> str:
        return os.path.join(
            pathlib.Path(__file__).parent.resolve().parent.resolve(),
            "resources",
            "seedsigner-translations",
            "l10n"
        )


    @classmethod
    def has_bundled_assets(cls) -> bool:
        return len(glob.glob(os.path.join(cls.bundled_l10n_dir(), "*", "LC_MESSAGES", "messages.mo"))) > 0


    @classmethod
    def gettext_localedir(cls) -> str:
        if cls.has_bundled_assets():
            return cls.bundled_l10n_dir()
        return cls.CACHE_DIR


    @classmethod
    def get_external_locales(cls) -> set:
        """
        Locales available in external mode: on the microsd card and/or already copied
        into the tmpfs cache (usable even with the card removed).
        """
        locales = set()
        for base_dir in [cls.EXTERNAL_L10N_DIR, cls.CACHE_DIR]:
            for mo_file in glob.glob(os.path.join(base_dir, "*", "LC_MESSAGES", "messages.mo")):
                locales.add(mo_file.rsplit(os.sep, 3)[-3])
        return locales


    @classmethod
    def refresh_cache(cls, locale: str) -> bool:
        """
        External mode only: copy `locale`'s messages.mo and font (if it needs one)
        from the microsd card into the tmpfs cache.

        Returns True if the locale's assets are ready to use. Previously cached
        locales are intentionally never deleted (full payload is ~2 MB) so the user
        can switch back to an already-used locale with the card removed.
        """
        # Import here to avoid a circular import (see note at top of module)
        from seedsigner.models.settings_definition import SettingsConstants

        if cls.has_bundled_assets():
            return True

        if locale == SettingsConstants.LOCALE__ENGLISH:
            # English is embedded in the app source; no assets needed
            return True

        rel_mo_path = os.path.join(locale, "LC_MESSAGES", "messages.mo")
        if not cls._copy_asset(
                os.path.join(cls.EXTERNAL_L10N_DIR, rel_mo_path),
                os.path.join(cls.CACHE_DIR, rel_mo_path)):
            return False

        # Import here to avoid a circular import (components imports settings which
        # imports this module)
        from seedsigner.gui.components import Fonts, GUIConstants

        font_name = GUIConstants.BASE_LOCALE_FONTS.get(locale)
        if font_name and font_name != GUIConstants.BASE_LOCALE_FONTS["default"]:
            font_filename = f"{font_name}.ttf"
            if not cls._copy_asset(
                    os.path.join(cls.EXTERNAL_L10N_DIR, "fonts", font_filename),
                    os.path.join(cls.CACHE_FONTS_DIR, font_filename)):
                return False

            # Evict any cached fallback font loaded before this font was available
            Fonts.fonts.pop(font_name, None)

        return True


    @classmethod
    def _copy_asset(cls, src: str, dest: str) -> bool:
        """
        Copies via a temp file + os.replace so a partially-written file (e.g. the
        card is yanked mid-copy) never lands at `dest`.
        """
        try:
            if os.path.getsize(src) > cls.MAX_ASSET_FILE_SIZE:
                logger.error(f"l10n asset too large, ignoring: {src}")
                return False
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            tmp_dest = dest + ".tmp"
            shutil.copyfile(src, tmp_dest)
            os.replace(tmp_dest, dest)
            return True
        except OSError as e:
            logger.warning(f"Could not copy l10n asset {src}: {e}")
            return False
