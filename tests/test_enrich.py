import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jobscan.enrich import (employment_type, locate, normalize_job_url, role_families,  # noqa: E402
                            seniority, tech_tags, to_row)


class NormalizeUrl(unittest.TestCase):
    # Expected values match the extension's normalizeJobUrl (URL.toString output).
    def test_drops_tracking_and_hash(self):
        self.assertEqual(normalize_job_url("https://Jobs.Lever.co/acme/123/apply?lever-source=x#top"),
                         "https://jobs.lever.co/acme/123/apply")
        self.assertEqual(normalize_job_url("https://jobs.lever.co/acme/123/application/?utm_source=x"),
                         "https://jobs.lever.co/acme/123")

    def test_keeps_job_id_param(self):
        self.assertEqual(normalize_job_url("https://boards.greenhouse.io/acme?gh_jid=42&gh_src=abc"),
                         "https://boards.greenhouse.io/acme?gh_jid=42")

    def test_root_and_junk(self):
        self.assertEqual(normalize_job_url("https://example.com"), "https://example.com/")
        self.assertEqual(normalize_job_url("not a url"), "not a url")
        self.assertEqual(normalize_job_url(""), "")


class Classify(unittest.TestCase):
    def test_roles(self):
        self.assertEqual(role_families("Senior Frontend Engineer"), ["frontend"])
        self.assertIn("fullstack", role_families("Full-Stack Developer (React/Node)"))
        self.assertEqual(role_families("Software Engineer"), ["software"])
        self.assertEqual(role_families("Account Executive, EMEA"), ["sales"])
        self.assertEqual(role_families("Barista"), ["other"])

    def test_seniority(self):
        self.assertEqual(seniority("Staff Software Engineer"), "staff_plus")
        self.assertEqual(seniority("Senior Product Manager"), "senior")
        self.assertEqual(seniority("Engineering Manager, Payments"), "manager")
        self.assertEqual(seniority("Junior Data Analyst"), "junior")
        self.assertEqual(seniority("Software Engineer"), "unknown")

    def test_tech_primary_needs_title_or_repeats(self):
        mentioned, primary = tech_tags("Frontend Engineer (React)", "We use PHP once. TypeScript, TypeScript, TypeScript.")
        self.assertIn("php", mentioned)
        self.assertNotIn("php", primary)
        self.assertIn("react", primary)
        self.assertIn("typescript", primary)

    def test_location(self):
        self.assertEqual(locate("Remote - Worldwide", None, "", "")[:2], ("remote", "worldwide"))
        w, scope, regions, countries, _ = locate("Remote, Europe", None, "", "")
        self.assertEqual((w, scope, regions), ("remote", "region", ["europe"]))
        w, scope, _, countries, cities = locate("Berlin", False, "Frontend Engineer", "")
        self.assertEqual((w, scope, countries, cities), ("onsite", None, ["de"], ["berlin"]))
        self.assertEqual(locate("Berlin (Hybrid)", False, "", "")[0], "hybrid")
        self.assertEqual(locate("Remote (US)", None, "", "")[1], "country")

    def test_employment(self):
        self.assertEqual(employment_type("Full-time", "Engineer"), "full_time")
        self.assertEqual(employment_type("", "Frontend Developer (Freelance)"), "contract")
        self.assertEqual(employment_type("", "Engineer"), "unknown")


class Row(unittest.TestCase):
    def test_row_is_compact_and_stable(self):
        p = {"source": "lever", "external_id": "abc", "slug": "acme", "company": "Acme", "title": "Frontend Engineer",
             "location": "Remote - Europe", "remote": True, "url": "https://jobs.lever.co/acme/abc?lever-source=li",
             "posted_at": "2026-11-01T00:00:00+00:00", "date_field": "createdAt", "description": "React " * 2000,
             "comp": "", "employment": "Full-time"}
        r = to_row(p)
        self.assertNotIn("description", r)
        self.assertLessEqual(len(r["snippet"]), 301)
        self.assertEqual(r["canonical_url"], "https://jobs.lever.co/acme/abc")
        self.assertTrue(r["posted_at_exact"])
        self.assertEqual(r["content_hash"], to_row(dict(p))["content_hash"])
        self.assertNotEqual(r["content_hash"], to_row(dict(p, title="Frontend Engineer II"))["content_hash"])

    def test_missing_external_id_falls_back_to_url_hash(self):
        r = to_row({"source": "weworkremotely", "title": "X", "url": "https://weworkremotely.com/x"})
        self.assertTrue(r["external_id"].startswith("u:"))
        self.assertFalse(to_row({"source": "greenhouse", "title": "X", "url": "u", "date_field": "updated_at"})["posted_at_exact"])


if __name__ == "__main__":
    unittest.main()
