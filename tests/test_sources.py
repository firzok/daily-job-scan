"""Unit tests for the new source adapters (smartrecruiters, bamboohr, personio, breezy,
jobicy). These exercise the parsing logic against hand-built fixtures matching each
platform's documented public response shape -- they do NOT hit the network, and cannot
substitute for running scripts/fetch_jobs.py --sources <name> against the real endpoint
once this merges (the daily workflow's own run is the first real-world check).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from jobscan.sources import (src_bamboohr, src_breezy, src_jobicy,  # noqa: E402
                              src_personio, src_smartrecruiters)


class FakeFetcher:
    """Returns a fixed payload for every .get() call, regardless of URL -- enough to
    unit test one adapter's parsing in isolation."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, kind="json"):
        self.calls.append(url)
        return self.payload


class SmartRecruiters(unittest.TestCase):
    def test_parses_one_page(self):
        payload = {
            "totalFound": 1, "offset": 0, "limit": 100,
            "content": [{
                "id": "abc123", "name": "Senior Backend Engineer",
                "company": {"identifier": "acme", "name": "Acme Inc"},
                "location": {"city": "Berlin", "region": "", "country": "Germany", "remote": True},
                "releasedDate": "2026-01-01T00:00:00.000Z",
                "postingUrl": "https://jobs.smartrecruiters.com/Acme/abc123",
                "typeOfEmployment": {"label": "Full-time"},
            }],
        }
        out = src_smartrecruiters(FakeFetcher(payload), "acme")
        self.assertEqual(len(out), 1)
        j = out[0]
        self.assertEqual(j["source"], "smartrecruiters")
        self.assertEqual(j["company"], "Acme Inc")
        self.assertEqual(j["title"], "Senior Backend Engineer")
        self.assertEqual(j["location"], "Berlin, Germany")
        self.assertTrue(j["remote"])
        self.assertEqual(j["url"], "https://jobs.smartrecruiters.com/Acme/abc123")
        self.assertEqual(j["employment"], "Full-time")
        self.assertIsNotNone(j["posted_at"])

    def test_stops_pagination_when_page_not_full(self):
        # offset(0) + limit(100) >= totalFound(1) -> fetch_all's "one page" fixture only
        # gets requested once even though the loop allows up to 3 pages.
        payload = {"totalFound": 1, "offset": 0, "limit": 100, "content": []}
        f = FakeFetcher(payload)
        src_smartrecruiters(f, "acme")
        self.assertEqual(len(f.calls), 1)


class BambooHR(unittest.TestCase):
    def test_parses_listing(self):
        payload = {"result": [{
            "id": "48", "jobOpeningName": "Product Designer", "locationLabel": "Remote",
            "isRemote": True, "employmentStatusLabel": "Full-Time",
        }], "count": 1}
        out = src_bamboohr(FakeFetcher(payload), "dovetail")
        self.assertEqual(len(out), 1)
        j = out[0]
        self.assertEqual(j["source"], "bamboohr")
        self.assertEqual(j["title"], "Product Designer")
        self.assertEqual(j["url"], "https://dovetail.bamboohr.com/careers/48")
        self.assertTrue(j["remote"])
        self.assertIsNone(j["posted_at"])  # no reliable date field at this list level


class Personio(unittest.TestCase):
    XML = """<?xml version="1.0" encoding="UTF-8"?>
<workzag-jobs>
  <position>
    <id>555</id>
    <name>Engineering Manager</name>
    <office>Munich</office>
    <employmentType>Permanent employee</employmentType>
    <createdAt>2026-02-01T00:00:00+01:00</createdAt>
    <jobDescriptions>
      <jobDescription>
        <name>Your Mission</name>
        <jobDescriptionValue>&lt;p&gt;Lead the team.&lt;/p&gt;</jobDescriptionValue>
      </jobDescription>
    </jobDescriptions>
  </position>
</workzag-jobs>"""

    def test_parses_position(self):
        out = src_personio(FakeFetcher(self.XML), "acme")
        self.assertEqual(len(out), 1)
        j = out[0]
        self.assertEqual(j["source"], "personio")
        self.assertEqual(j["title"], "Engineering Manager")
        self.assertEqual(j["location"], "Munich")
        self.assertEqual(j["url"], "https://acme.jobs.personio.de/job/555")
        self.assertIn("Lead the team.", j["description"])
        self.assertIsNotNone(j["posted_at"])


class Breezy(unittest.TestCase):
    def test_parses_list(self):
        payload = [{
            "id": "xyz789", "name": "Staff Engineer", "type": "Full-Time",
            "location": {"name": "Remote", "is_remote": True},
            "published_date": "2026-03-01T00:00:00.000Z",
            "description": "<p>Join us.</p>",
        }]
        out = src_breezy(FakeFetcher(payload), "acme")
        self.assertEqual(len(out), 1)
        j = out[0]
        self.assertEqual(j["source"], "breezy")
        self.assertEqual(j["title"], "Staff Engineer")
        self.assertEqual(j["url"], "https://acme.breezy.hr/p/xyz789")
        self.assertTrue(j["remote"])
        self.assertIn("Join us.", j["description"])


class Jobicy(unittest.TestCase):
    def test_parses_jobs(self):
        payload = {"jobCount": 1, "jobs": [{
            "id": 42, "jobTitle": "Remote Frontend Developer", "companyName": "Acme",
            "jobGeo": "Worldwide", "url": "https://jobicy.com/jobs/42-remote-frontend-developer",
            "pubDate": "2026-04-01 00:00:00",
            "jobDescription": "<p>Build things.</p>",
            "jobType": ["full-time"],
            "annualSalaryMin": 80000, "annualSalaryMax": 120000, "salaryCurrency": "USD",
        }]}
        out = src_jobicy(FakeFetcher(payload))
        self.assertEqual(len(out), 1)
        j = out[0]
        self.assertEqual(j["source"], "jobicy")
        self.assertEqual(j["title"], "Remote Frontend Developer")
        self.assertTrue(j["remote"])
        self.assertEqual(j["employment"], "full-time")
        self.assertIn("80000", j["comp"])
        self.assertIsNotNone(j["posted_at"])


if __name__ == "__main__":
    unittest.main()
