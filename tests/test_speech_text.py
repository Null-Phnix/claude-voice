"""Speech text: user pronunciations, markdown cleanup and dev syntax."""

import unittest

import claude_voice as cv


class UserPronunciationTests(unittest.TestCase):
    def test_user_entries_override_and_extend_builtins(self):
        extra = {"pytest": "pie test", "JSON": "jay son"}
        self.assertEqual(cv.fix_pronunciation("run pytest on JSON", extra),
                         "run pie test on jay son")

    def test_longest_term_wins_and_replacements_are_not_resubstituted(self):
        extra = {"PostgreSQL": "post gres Q L", "Postgres": "post gres"}
        self.assertEqual(cv.fix_pronunciation("PostgreSQL and Postgres", extra),
                         "post gres Q L and post gres")

    def test_terms_starting_with_punctuation_match(self):
        self.assertEqual(cv.fix_pronunciation("use .NET here", {".NET": "dot net"}),
                         "use dot net here")


class CleanForSpeechTests(unittest.TestCase):
    def test_short_inline_code_is_spoken(self):
        self.assertEqual(cv.clean_for_speech("Run `pytest` now."), "Run pytest now.")

    def test_inline_paths_are_kept_for_the_path_rule(self):
        self.assertEqual(cv.clean_for_speech("Edit `~/.config/x/config.json`."),
                         "Edit ~/.config/x/config.json.")

    def test_flags_and_csharp_survive_cleanup(self):
        self.assertEqual(cv.clean_for_speech("## Run\nUse `--dry-run` -- in C# too"),
                         "Run Use --dry-run in C# too")

    def test_long_inline_code_is_dropped(self):
        long = "x" * 80
        self.assertEqual(cv.clean_for_speech(f"See `{long}` here."), "See here.")

    def test_tables_read_as_rows(self):
        table = "| Voice | Sound |\n|---|---|\n| af_heart | warm |\n"
        self.assertEqual(cv.clean_for_speech(table), "Voice, Sound. af_heart, warm.")

    def test_emphasis_underscores_go_but_snake_case_stays(self):
        self.assertEqual(cv.clean_for_speech("set max_chars and _emphasis_"),
                         "set max_chars and emphasis")


class SpokenSyntaxTests(unittest.TestCase):
    def say(self, text, extra=None):
        return cv.fix_pronunciation(text, extra or {})

    def test_flags(self):
        self.assertEqual(self.say("run it --no-daemon -v and rm -rf x"),
                         "run it dash dash no daemon dash V and rm dash R F x")
        self.assertEqual(self.say("pass --output=out"), "pass dash dash output equals out")

    def test_hyphenated_words_and_negative_numbers_are_not_flags(self):
        self.assertEqual(self.say("a well-known -5 offset"), "a well-known -5 offset")

    def test_slash_commands_and_paths(self):
        self.assertEqual(self.say("type /voice resume."), "type slash voice resume.")
        self.assertEqual(self.say("edit ~/.config/app/config.json."),
                         "edit home slash dot config slash app slash config dot jason.")
        self.assertEqual(self.say("see /etc/fstab, then ./build/."),
                         "see slash etc slash fstab, then dot slash build.")

    def test_prose_slashes_are_not_paths(self):
        for text in ("and/or", "I/O", "client/server", "24/7"):
            with self.subTest(text=text):
                self.assertEqual(self.say(text), text)

    def test_long_paths_keep_the_tail(self):
        self.assertEqual(self.say("/a/b/c/d/e/f.py"), "d slash e slash f dot pie")

    def test_extensions_dotfiles_and_domains(self):
        self.assertEqual(self.say("edit main.rs and .env on github.com."),
                         "edit main dot R S and dot env on github dot com.")

    def test_every_known_extension_in_a_chain(self):
        self.assertEqual(self.say("see index.d.ts."), "see index dot D dot T S.")

    def test_optional_chaining_and_nullish(self):
        self.assertEqual(self.say("user?.name ?? fallback"), "user optional name or fallback")

    def test_unknown_extensions_and_versions_untouched(self):
        self.assertEqual(self.say("v1.2.3 and foo.zzzzz"), "v1.2.3 and foo.zzzzz")

    def test_lexicon_keys_with_punctuation_are_protected(self):
        extra = {"Next.js": "next J S", "CI/CD": "C I C D", "pytest": "pie test"}
        self.assertEqual(self.say("Next.js CI/CD pytest.ini", extra),
                         "next J S C I C D pie test dot ini")

    def test_sentence_start_capital_matches_lowercase_key(self):
        self.assertEqual(self.say("Pytest ran.", {"pytest": "pie test"}), "pie test ran.")
        self.assertEqual(self.say("PYTEST ran.", {"pytest": "pie test"}), "PYTEST ran.")

    def test_snake_case_split_after_lexicon(self):
        extra = {"x86_64": "x eighty-six sixty-four"}
        self.assertEqual(self.say("max_chars on x86_64", extra),
                         "max chars on x eighty-six sixty-four")

    def test_camel_case_split_then_lexicon(self):
        self.assertEqual(self.say("getUserName and parseJSON and HTTPServer on APIs"),
                         "get User Name and parse jason and HTTP Server on APIs")

    def test_symbols(self):
        self.assertEqual(self.say("e.g. $HOME, #42, @scope, std::vector, a && b"),
                         "for example dollar HOME, number 42, at scope, std vector, a and b")


if __name__ == "__main__":
    unittest.main()
