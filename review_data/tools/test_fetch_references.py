import unittest

from review_data.tools import fetch_references


class TestFetchReferences(unittest.TestCase):
    def review(self, login: str, state: str, oid: str, body: str = "", typename: str = "User") -> dict:
        return {"author": {"login": login, "__typename": typename}, "state": state, "body": body,
                "submittedAt": "t", "commit": {"oid": oid},
                "comments": {"nodes": [{"path": "a.py", "line": 3, "originalLine": 3, "body": "c", "diffHunk": "@@"}]}}

    def test_picks_the_latest_human_review_of_the_label_state_on_the_head(self):
        reviews = [self.review("m1", "CHANGES_REQUESTED", "h", "old"),
                   self.review("github-actions", "CHANGES_REQUESTED", "h", "agent"),
                   self.review("m2", "CHANGES_REQUESTED", "h", "new"),
                   self.review("m3", "CHANGES_REQUESTED", "other", "other head"),
                   self.review("m4", "APPROVED", "h")]
        picked = fetch_references.pick(reviews, {"head_sha": "h", "label": "block"})
        ref = fetch_references.reference({"pr": 1, "head_sha": "h", "label": "block"}, picked)
        self.assertEqual((ref["decision"]["author"], ref["decision"]["body"]), ("m2", "new"))
        self.assertEqual(ref["inline_comments"][0]["body"], "c")
        self.assertEqual(fetch_references.pick(reviews, {"head_sha": "h", "label": "clean"})["author"]["login"], "m4")
        self.assertIsNone(fetch_references.reference({"pr": 1, "head_sha": "x", "label": "block"}, None)["decision"])

    def test_picks_the_review_a_case_names_and_keeps_outdated_flags(self):
        commented = self.review("m5", "COMMENTED", "h", "please simplify")
        commented["submittedAt"] = "t2"
        commented["comments"]["nodes"][0]["outdated"] = True
        reviews = [self.review("m2", "CHANGES_REQUESTED", "h"), commented]
        case = {"pr": 1, "head_sha": "h", "label": "block", "review": {"login": "m5", "submitted_at": "t2"}}
        ref = fetch_references.reference(case, fetch_references.pick(reviews, case))
        self.assertEqual((ref["decision"]["author"], ref["decision"]["state"]), ("m5", "COMMENTED"))
        self.assertIs(ref["inline_comments"][0]["outdated"], True)


if __name__ == "__main__":
    unittest.main()
