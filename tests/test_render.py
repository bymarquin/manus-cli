import io
import unittest

from rich.console import Console

from manus_cli import render


class SupportsUnicodeTests(unittest.TestCase):
    def test_utf8_supports_the_glyphs(self):
        self.assertTrue(render._supports_unicode("utf-8"))

    def test_ascii_and_windows_codepages_do_not(self):
        self.assertFalse(render._supports_unicode("ascii"))
        self.assertFalse(render._supports_unicode("cp1252"))
        self.assertFalse(render._supports_unicode("cp437"))

    def test_missing_encoding_defaults_to_capable(self):
        # arquivo sem atributo "encoding" (ex: alguns pipes/mocks) — assume capaz,
        # já que a maioria dos consoles modernos é utf-8.
        self.assertTrue(render._supports_unicode(None))

    def test_unknown_encoding_name_falls_back_safely(self):
        self.assertFalse(render._supports_unicode("not-a-real-encoding"))


class GlyphFallbackRenderingTests(unittest.TestCase):
    """Simula um console que não consegue codificar os glifos Unicode (ex: binário
    PyInstaller no Windows com stdout redirecionado em cp1252) e garante que a
    impressão usa o equivalente ASCII em vez de estourar UnicodeEncodeError."""

    def setUp(self):
        self.orig_console = render.console
        self.orig_err_console = render.err_console
        self.orig_prompt = render.PROMPT
        self.orig_check = render._CHECK
        self.orig_cross = render._CROSS
        self.orig_warn = render._WARN
        self.orig_dash = render._DASH

    def tearDown(self):
        render.console = self.orig_console
        render.err_console = self.orig_err_console
        render.PROMPT = self.orig_prompt
        render._CHECK = self.orig_check
        render._CROSS = self.orig_cross
        render._WARN = self.orig_warn
        render._DASH = self.orig_dash

    def _force_ascii_fallback(self):
        render.PROMPT = ">"
        render._CHECK = "+"
        render._CROSS = "x"
        render._WARN = "!"
        render._DASH = "-"

    def test_print_success_uses_ascii_fallback(self):
        self._force_ascii_fallback()
        buf = io.StringIO()
        render.console = Console(file=buf, force_terminal=False, no_color=True, theme=render._THEME)
        render.print_success("ok")
        output = buf.getvalue()
        self.assertIn("+", output)
        self.assertNotIn("✓", output)

    def test_print_fail_and_warning_use_ascii_fallback(self):
        self._force_ascii_fallback()
        buf = io.StringIO()
        render.err_console = Console(file=buf, force_terminal=False, no_color=True, theme=render._THEME)
        render.print_fail("deu ruim")
        render.print_warning("cuidado")
        output = buf.getvalue()
        self.assertIn("x deu ruim", output)
        self.assertIn("! cuidado", output)
        self.assertNotIn("✗", output)
        self.assertNotIn("⚠", output)

    def test_print_dry_run_uses_ascii_fallback_for_all_markers(self):
        self._force_ascii_fallback()
        buf = io.StringIO()
        render.console = Console(file=buf, force_terminal=False, no_color=True, theme=render._THEME)

        class _F:
            def __init__(self, name, size):
                from pathlib import Path

                self.relative_path = Path(name)
                self.size = size

        class _S:
            def __init__(self, name, reason):
                from pathlib import Path

                self.relative_path = Path(name)
                self.reason = reason

        render.print_dry_run([_F("a.txt", 10)], [_S("b.env", "segredo")], 10)
        output = buf.getvalue()
        self.assertIn("+ a.txt", output)
        self.assertIn("- b.env", output)
        self.assertNotIn("✓", output)
        self.assertNotIn("—", output)

    def test_unicode_glyphs_used_when_console_supports_them(self):
        # comportamento padrão (sem forçar fallback): glifos Unicode reais.
        buf = io.StringIO()
        render.console = Console(file=buf, force_terminal=False, no_color=True, theme=render._THEME)
        render.print_success("ok")
        self.assertIn(render._CHECK, buf.getvalue())


if __name__ == "__main__":
    unittest.main()
