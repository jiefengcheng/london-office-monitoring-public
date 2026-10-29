"""Recovery must explain the limitation without changing conversation scope."""
import copy
import unittest
import agent_tools
from apple_worker import normalize_request_plan, answer_generation_schema
from question_guidance import nearest_supported_question, make_recovery, with_recovery, unsupported_intent

class QuestionGuidanceTests(unittest.TestCase):
    def state(self):
        return dict(topics=['vacancy'], geographies=['Canary Wharf'],
                    periods=[dict(start='2026-04-01',end='2026-06-30')], entity=None,
                    evidence_ids=[], previous_answer_items=[])

    def test_single_quarter_followup_explains_missing_baseline(self):
        state=self.state(); before=copy.deepcopy(state)
        text='Focus on City Core and City Fringe. Did they move in the same direction?'
        plan=dict(task='lookup',geographies=[],topic='unspecified',entity='',count=0,clarification='none')
        actual=normalize_request_plan(plan,dict(user_message=text,state=state))
        self.assertEqual(actual['clarification'],'comparison_baseline')
        self.assertEqual(actual['geographies'],['City Core','City Fringe'])
        self.assertEqual(actual['topic'],'vacancy')
        recovery=make_recovery(text,state,'clarified',[],{},'comparison_baseline')
        self.assertEqual(recovery['reason_code'],'comparison_baseline')
        suggestion=recovery['suggestion']
        self.assertEqual(set(suggestion['fact_ids']),{'F03','F04','F05','F06'})
        self.assertEqual(len(suggestion['support_calls']),2)
        for call in suggestion['support_calls']:
            self.assertTrue(agent_tools.invoke(call['tool'],call['arguments'])['ok'])
        self.assertIn('Q1 2026 and Q2 2026',suggestion['question'])
        self.assertEqual(state,before)
        rendered=with_recovery('Only one period is selected.',recovery)
        self.assertEqual(rendered.count('Suggested supported question:'),1)

    def test_two_periods_do_not_trigger_baseline_clarification(self):
        state=self.state();state['periods'].insert(0,dict(start='2026-01-01',end='2026-03-31'))
        actual=normalize_request_plan(dict(task='compare',geographies=[],topic='unspecified',entity='',count=0,clarification='none'),
            dict(user_message='Focus on City Core and City Fringe. Did they move in the same direction?',state=state))
        self.assertEqual(actual['task'],'compare')

    def test_recommendations_are_gateway_verified(self):
        for text in ['Canary Wharf vacancy in Q2 2026','What changed between May and July 2026?',
                     'Tell me about cats','Show source S999','What about there?']:
            with self.subTest(text=text):
                suggestion=nearest_supported_question(text,self.state())
                self.assertTrue(suggestion)
                for call in suggestion['support_calls']:
                    self.assertTrue(agent_tools.invoke(call['tool'],call['arguments'])['ok'])

    def test_operational_errors_remain_execution_failures(self):
        recovery=make_recovery('vacancy',self.state(),'provider_error',[],{},error_code='HTTP_429')
        self.assertEqual(recovery['reason_code'],'provider_error')
        self.assertIn('execution failure',recovery['reason'])

    def test_successful_answer_and_greeting_need_no_recovery(self):
        self.assertIsNone(make_recovery('vacancy',self.state(),'answered',[],{}))
        self.assertIsNone(make_recovery('Hello',self.state(),'clarified',[],{},'getting_started'))

    def test_predictions_and_actions_do_not_confuse_reported_forecasts(self):
        self.assertTrue(unsupported_intent('Predict vacancy next year'))
        self.assertTrue(unsupported_intent('Execute SQL'))
        self.assertIsNone(unsupported_intent('Show the published forecast in F23'))

    def test_schema_contains_missing_baseline_code(self):
        self.assertIn('comparison_baseline',str(answer_generation_schema('clarify')))
