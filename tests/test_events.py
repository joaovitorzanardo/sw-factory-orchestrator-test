import unittest

from software_factory.config import DEFAULT_TRIGGER_LABELS
from software_factory.linear.events import parse_issue, should_process
from tests.payloads import BUG, DOCS, FEATURE, issue_payload


def process(payload) -> bool:
    return should_process(payload, DEFAULT_TRIGGER_LABELS)


class TestShouldProcess(unittest.TestCase):
    def test_create_with_feature(self) -> None:
        self.assertTrue(process(issue_payload(labels=[FEATURE])))

    def test_create_with_bug_lowercase(self) -> None:
        self.assertTrue(process(issue_payload(labels=[BUG, DOCS])))

    def test_create_without_trigger_label(self) -> None:
        self.assertFalse(process(issue_payload(labels=[DOCS])))
        self.assertFalse(process(issue_payload(labels=[])))

    def test_update_adding_trigger_label(self) -> None:
        payload = issue_payload(
            "update", labels=[DOCS, FEATURE], updated_from={"labelIds": [DOCS["id"]]}
        )
        self.assertTrue(process(payload))

    def test_update_label_already_present(self) -> None:
        payload = issue_payload(
            "update", labels=[FEATURE, DOCS], updated_from={"labelIds": [FEATURE["id"]]}
        )
        self.assertFalse(process(payload))

    def test_update_other_field(self) -> None:
        payload = issue_payload("update", labels=[FEATURE], updated_from={"title": "Antigo"})
        self.assertFalse(process(payload))

    def test_remove(self) -> None:
        self.assertFalse(process(issue_payload("remove", labels=[FEATURE])))

    def test_non_issue_type(self) -> None:
        self.assertFalse(process(issue_payload(labels=[FEATURE], type_="Comment")))


class TestParseIssue(unittest.TestCase):
    def test_parse(self) -> None:
        issue = parse_issue(issue_payload(labels=[FEATURE, DOCS]))
        self.assertEqual(issue.id, "issue-uuid")
        self.assertEqual(issue.identifier, "ENG-12")
        self.assertEqual(issue.title, "Título")
        self.assertEqual(issue.team_key, "ENG")
        self.assertEqual(issue.labels, ("Feature", "Docs"))
        self.assertEqual(issue.url, "https://linear.app/acme/issue/ENG-12/titulo")


if __name__ == "__main__":
    unittest.main()
