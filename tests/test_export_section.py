import unittest

from onenote_export.exporter import (
    Notebook,
    Section,
    _matching_section_pairs,
    _resolve_single_section,
)


def _nb(id_: str, name: str) -> Notebook:
    return Notebook(id=id_, name=name)


def _sec(id_: str, name: str, nb_id: str, parent_sg: str | None = None) -> Section:
    return Section(
        id=id_,
        name=name,
        parent_notebook_id=nb_id,
        parent_section_group_id=parent_sg,
    )


class SingleSectionTests(unittest.TestCase):
    def test_resolve_single_match(self) -> None:
        n1 = _nb("nb1", "Work")
        s1 = _sec("s1", "Chapter A", "nb1")
        notebook_by_id = {"nb1": n1}
        sections = [s1]
        pair = _resolve_single_section(
            notebook_by_id=notebook_by_id,
            sections=sections,
            notebook_filters={"work"},
            section_filters={"chapter a"},
        )
        self.assertEqual(pair[0].name, "Work")
        self.assertEqual(pair[1].name, "Chapter A")

    def test_resolve_zero_raises(self) -> None:
        n1 = _nb("nb1", "Work")
        s1 = _sec("s1", "Chapter A", "nb1")
        with self.assertRaises(RuntimeError) as ctx:
            _resolve_single_section(
                notebook_by_id={"nb1": n1},
                sections=[s1],
                notebook_filters={"other"},
                section_filters=None,
            )
        self.assertIn("no section matched", str(ctx.exception).lower())

    def test_resolve_multiple_raises(self) -> None:
        n1 = _nb("nb1", "Work")
        s1 = _sec("s1", "Meetings", "nb1")
        s2 = _sec("s2", "Meetings", "nb1")
        with self.assertRaises(RuntimeError) as ctx:
            _resolve_single_section(
                notebook_by_id={"nb1": n1},
                sections=[s1, s2],
                notebook_filters={"work"},
                section_filters={"meetings"},
            )
        self.assertIn("exactly one", str(ctx.exception).lower())

    def test_matching_pairs_respects_filters(self) -> None:
        n1 = _nb("nb1", "A")
        n2 = _nb("nb2", "B")
        s1 = _sec("s1", "One", "nb1")
        s2 = _sec("s2", "Two", "nb2")
        pairs = _matching_section_pairs(
            {"nb1": n1, "nb2": n2},
            [s1, s2],
            notebook_filters={"b"},
            section_filters=None,
        )
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][1].id, "s2")


if __name__ == "__main__":
    unittest.main()
