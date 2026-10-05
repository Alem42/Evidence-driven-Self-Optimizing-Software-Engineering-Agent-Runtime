"""Diagnoser 的逐条核对与确定性改判。The Diagnoser's per-case checks and the deterministic reconciliation."""
import unittest

from masa.agents.schemas import response_schema
from masa.domain.models import MasaError
from masa.domain.proposals import reconcile_diagnosis, validate_diagnosis


def check(case, matches, says='food 1.00 comes first', expects='clothing comes first'):
    return {'case': case, 'requirement_says': says, 'test_expects': expects, 'matches': matches}


def verdict(owner, checks=None, **over):
    base = {'owner': owner, 'rationale': 'r', 'implementation_instructions': 'fix impl' if owner in ('implementation', 'both') else '',
            'test_instructions': 'fix test' if owner in ('test', 'both') else ''}
    if checks is not None:
        base['expectation_checks'] = checks
    base.update(over)
    return base


class ValidateTests(unittest.TestCase):
    def test_old_four_field_answers_still_validate_and_get_empty_checks(self):
        self.assertEqual(validate_diagnosis(verdict('implementation'))['expectation_checks'], [])

    def test_checks_are_validated(self):
        ok = validate_diagnosis(verdict('implementation', [check('a', True)]))
        self.assertEqual(ok['expectation_checks'][0]['matches'], True)
        for bad in ([{'case': 'a'}], [check('a', 'yes')], [check('a', True)] * 9, 'x'):
            with self.subTest(bad=str(bad)[:30]), self.assertRaises(MasaError):
                validate_diagnosis(verdict('implementation', bad))
        with self.assertRaises(MasaError):
            validate_diagnosis(verdict('implementation', [check('a', True, says='x' * 301)]))

    def test_the_schema_makes_the_model_state_the_requirement_before_the_verdict(self):
        schema = response_schema({'purpose': 'project_diagnoser'})
        self.assertEqual(list(schema['properties'])[:2], ['expectation_checks', 'owner'])  # 先核对、后下结论 / checks first, verdict after
        item = schema['properties']['expectation_checks']['items']
        self.assertEqual(list(item['properties']), ['case', 'requirement_says', 'test_expects', 'matches'])  # 先写需求怎么说 / the requirement first


class ReconcileTests(unittest.TestCase):
    def test_when_every_checked_case_disagrees_the_test_is_blamed_even_if_the_verdict_said_implementation(self):
        # 真实任务（CSV）：测试把“按金额降序”写成了升序，Diagnoser 却判成实现问题。
        wrong = validate_diagnosis(verdict('implementation', [check('food vs clothing', False), check('three categories', False)]))
        fixed, changed = reconcile_diagnosis(wrong)
        self.assertTrue(changed)
        self.assertEqual(fixed['owner'], 'test')
        self.assertIn('the requirement says food 1.00 comes first', fixed['test_instructions'])
        self.assertIn('food vs clothing', fixed['test_instructions'])

    def test_a_partial_disagreement_adds_the_test_side_to_an_implementation_verdict(self):
        fixed, changed = reconcile_diagnosis(validate_diagnosis(verdict('implementation', [check('a', False), check('b', True)])))
        self.assertTrue(changed)
        self.assertEqual(fixed['owner'], 'both')
        self.assertTrue(fixed['implementation_instructions'] and fixed['test_instructions'])

    def test_agreement_missing_checks_and_human_verdicts_are_left_alone(self):
        for diagnosis in (validate_diagnosis(verdict('implementation', [check('a', True)])), validate_diagnosis(verdict('implementation')),
                          validate_diagnosis(verdict('spec', [check('a', False)])), validate_diagnosis(verdict('unclear', [check('a', False)]))):
            self.assertEqual(reconcile_diagnosis(diagnosis), (diagnosis, False))

    def test_a_test_verdict_with_all_mismatches_is_not_changed(self):
        diagnosis = validate_diagnosis(verdict('test', [check('a', False)]))
        self.assertEqual(reconcile_diagnosis(diagnosis), (diagnosis, False))


if __name__ == '__main__':
    unittest.main()
