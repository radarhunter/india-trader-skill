import json, os, re, unittest
from _load import ROOT

SKILLS = ["nse-scanner", "nse-news", "nse-entry", "nse-exit", "nse-timemachine", "nse-budget", "nse-risk",
          "nse-broker", "nse-alerts", "nse-results", "nse-insider", "nse-picks"]
ORDER_PATH = ["nse-scanner", "nse-entry", "nse-exit", "nse-timemachine", "nse-budget", "nse-risk", "nse-broker", "nse-alerts", "nse-picks"]


def frontmatter(path):
    text = open(path, encoding="utf-8").read()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    return (m.group(1) if m else ""), text


class Repo(unittest.TestCase):
    def test_twelve_skill_folders(self):
        found = sorted(d for d in os.listdir(ROOT) if os.path.isfile(os.path.join(ROOT, d, "SKILL.md")))
        self.assertEqual(found, sorted(SKILLS))

    def test_common_lib_present(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "nse-common", "nse_common.py")))

    def test_frontmatter(self):
        for s in SKILLS:
            fm, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            self.assertIn("name: %s\n" % s, fm + "\n", s)
            self.assertGreater(len(fm), 300, s)
            self.assertGreater(len(text.splitlines()), 50, s)

    def test_order_path_carries_safety(self):
        for s in ORDER_PATH:
            _, text = frontmatter(os.path.join(ROOT, s, "SKILL.md"))
            low = " ".join(text.lower().split())
            self.assertIn("not financial advice", low, s)
            self.assertIn("sebi", low, s)

    def test_plugin_manifest(self):
        doc = json.load(open(os.path.join(ROOT, ".claude-plugin", "plugin.json")))
        self.assertEqual(doc["name"], "india-trader-skill")
        self.assertEqual(doc["skills"], "./")
        mk = json.load(open(os.path.join(ROOT, ".claude-plugin", "marketplace.json")))
        self.assertEqual(mk["plugins"][0]["source"], "./")

    def test_no_secrets_in_tree(self):
        rx = re.compile(r"(sk-[A-Za-z0-9]{20,}|ANGEL_TOTP_SECRET\s*=\s*['\"][A-Z2-7]{16,})")
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            for f in files:
                if f.endswith((".py", ".md", ".json")):
                    self.assertIsNone(rx.search(open(os.path.join(base, f), encoding="utf-8", errors="ignore").read()), f)


if __name__ == "__main__":
    unittest.main()
