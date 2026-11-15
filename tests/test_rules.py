import unittest

from usbcopyer.rules import (
    build_folder_name,
    ext_allowed,
    file_ext,
    parse_exclude_patterns,
    parse_ext_list,
    parse_folder_rule,
    render_folder_rule,
    sanitize_name,
    should_exclude_dir,
)
from usbcopyer.winapi import VolumeInfo


def fake_volume(**kw):
    base = dict(letter="E", root="E:\\", label="MYUSB", serial="1A2B3C4D", filesystem="exFAT", drive_type="removable")
    base.update(kw)
    return VolumeInfo(**base)


class ParseRuleTests(unittest.TestCase):
    def test_single_token(self):
        parts = parse_folder_rule("[serial]")
        self.assertEqual([(p.kind, p.value) for p in parts], [("token", "serial")])

    def test_text_and_token(self):
        parts = parse_folder_rule("Backup-[name]")
        self.assertEqual(parts[0].kind, "text")
        self.assertEqual(parts[0].value, "Backup-")
        self.assertEqual((parts[1].kind, parts[1].value), ("token", "name"))

    def test_quoted_literal(self):
        parts = parse_folder_rule('"备份-" [name]')
        self.assertEqual(parts[0].value, "备份-")
        self.assertEqual(parts[1].kind, "text")  # 空格
        self.assertEqual((parts[2].kind, parts[2].value), ("token", "name"))

    def test_escape_quote(self):
        # @" 表示字面量里的双引号
        parts = parse_folder_rule('"a@"b"')
        self.assertEqual(parts[0].value, 'a"b')

    def test_escape_at(self):
        # @@ 表示字面量里的 @
        parts = parse_folder_rule('"a@@b"')
        self.assertEqual(parts[0].value, 'a@b')

    def test_literal_bracket(self):
        # [[ 转义成字面量 [，其余原样，整体渲染为 "[x]"
        parts = parse_folder_rule("[[x]")
        self.assertEqual(parts[0].value, "[x]")

    def test_unterminated_quote(self):
        with self.assertRaises(ValueError):
            parse_folder_rule('"abc')

    def test_unterminated_bracket(self):
        with self.assertRaises(ValueError):
            parse_folder_rule("[serial")

    def test_unknown_token(self):
        with self.assertRaises(ValueError):
            parse_folder_rule("[bogus]")

    def test_empty(self):
        with self.assertRaises(ValueError):
            parse_folder_rule("")


class RenderTests(unittest.TestCase):
    def test_render_all_tokens(self):
        vol = fake_volume()
        text = render_folder_rule(parse_folder_rule("[letter]-[serial]-[name]-[fs]-[type]"), vol)
        self.assertEqual(text, "E-1A2B3C4D-MYUSB-exFAT-可移动磁盘")

    def test_empty_label_fallback(self):
        vol = fake_volume(label="")
        self.assertEqual(render_folder_rule(parse_folder_rule("[name]"), vol), "UDisk")

    def test_sanitize_illegal_chars(self):
        self.assertEqual(sanitize_name('a/b:c*d?"e'), "a_b_c_d__e")

    def test_sanitize_trailing(self):
        self.assertEqual(sanitize_name("abc. "), "abc")

    def test_sanitize_empty(self):
        self.assertEqual(sanitize_name("   "), "UDisk")

    def test_build_folder_name(self):
        self.assertEqual(build_folder_name("[letter]_[serial]", fake_volume()), "E_1A2B3C4D")


class ExtensionTests(unittest.TestCase):
    def test_parse_ext_list(self):
        self.assertEqual(parse_ext_list("jpg, .PNG; mp4"), {"jpg", "png", "mp4"})
        self.assertEqual(parse_ext_list(""), set())

    def test_file_ext(self):
        self.assertEqual(file_ext("a/b/c.tar.gz"), "gz")
        self.assertEqual(file_ext("LICENSE"), "")
        self.assertEqual(file_ext(".gitignore"), "")
        self.assertEqual(file_ext("a."), "")

    def test_ext_no_extension(self):
        # 原版这里会抛异常导致无扩展名文件被跳过
        self.assertTrue(ext_allowed("LICENSE", "black", "exe", "", True))
        self.assertFalse(ext_allowed("LICENSE", "black", "exe", "", False))

    def test_ext_blacklist(self):
        self.assertFalse(ext_allowed("a.exe", "black", "exe,msi", "", True))
        self.assertTrue(ext_allowed("a.txt", "black", "exe,msi", "", True))

    def test_ext_whitelist(self):
        self.assertTrue(ext_allowed("a.JPG", "white", "", "jpg,png", True))
        self.assertFalse(ext_allowed("a.txt", "white", "", "jpg,png", True))

    def test_ext_all(self):
        self.assertTrue(ext_allowed("a.exe", "all", "exe", "jpg", True))


class ExcludeTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(
            parse_exclude_patterns("node_modules, .git;\nSystem Volume Information"),
            ["node_modules", ".git", "system volume information"],
        )

    def test_name_matches_any_depth(self):
        patterns = parse_exclude_patterns("node_modules")
        self.assertTrue(should_exclude_dir("web\\\\node_modules", "node_modules", patterns))
        self.assertFalse(should_exclude_dir("web\\\\src", "src", patterns))

    def test_relative_path(self):
        patterns = parse_exclude_patterns("photos\\\\2020")
        self.assertTrue(should_exclude_dir("photos\\\\2020", "2020", patterns))
        self.assertFalse(should_exclude_dir("photos\\\\2021", "2021", patterns))

    def test_wildcard(self):
        patterns = parse_exclude_patterns("cache*")
        self.assertTrue(should_exclude_dir("a\\\\cachex", "cachex", patterns))
        self.assertFalse(should_exclude_dir("a\\\\data", "data", patterns))

    def test_empty_patterns(self):
        self.assertFalse(should_exclude_dir("a", "a", []))

if __name__ == "__main__":
    unittest.main()
