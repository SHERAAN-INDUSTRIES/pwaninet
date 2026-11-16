import copy
import unittest
from pwanimate.assessment_validation import (
    verdict, numerical, number, validate_questions, grade, metrics, REFERENCES,
)

class ValidationTests(unittest.TestCase):
    def question(self):
        return dict(prompt=REFERENCES['det-diag-234-v1']['prompt'], options=['6','12','24','48'],
                    answer='24', reference_id='det-diag-234-v1')

    def test_strict_boolean_verdicts(self):
        for text, expected in [('true',True),('false',False)]:
            self.assertIs(verdict('{"correct":'+text+'}')['correct'], expected)
        for value in ['"false"','"true"','null','1','0','[]']:
            self.assertEqual(verdict('{"correct":'+value+'}')['outcome'],'inconclusive')

    def test_malformed_verdicts(self):
        for text in ['', '{}', 'prose {"correct":true}', '{"correct":true,"correct":false}',
                     '{"correct":NaN}', '{"correct":true,"unexpected":1}', '{"correct":']:
            self.assertIsNone(verdict(text)['correct'])

    def test_verified_reference_and_invalid_selection(self):
        item=validate_questions([self.question()],1,'mcq')[0]
        self.assertIs(grade(item,'c')['correct'],True)
        self.assertIs(grade(item,'12')['correct'],False)
        self.assertEqual(grade(item,'other')['outcome'],'invalid')

    def test_wrong_reference_key(self):
        q=self.question();q['answer']='12'
        with self.assertRaises(ValueError):validate_questions([q],1,'mcq')

    def test_unverified_keys_never_award_credit(self):
        q=self.question();q.pop('reference_id')
        item=validate_questions([q],1,'mcq')[0]
        self.assertEqual(grade(item,'24')['outcome'],'inconclusive')
        item['reference_id']='det-diag-234-v1';item['answer_key']='12'
        self.assertEqual(grade(item,'24')['outcome'],'evaluation_failed')

    def test_generated_question_validation(self):
        changes=[{'prompt':''},{'answer':''},{'options':['a','a','b','c']},
                 {'options':['a','b','c','d'],'answer':'missing'},
                 {'options':[{'id':'a','label':str(i)} for i in range(4)]}]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(ValueError):
                validate_questions([dict(self.question(),**change)],1,'mcq')
        for questions,count in [([],1),([self.question()],2),([self.question()],True),
                                ([dict(self.question(),qid='same')]*2,2)]:
            with self.assertRaises(ValueError):validate_questions(questions,count,'mcq')

    def test_numeric_equivalence(self):
        for answer in ['1/2','0.5','5e-1']:
            self.assertIs(numerical(answer,'0.5','0','0')['correct'],True)
        self.assertIs(numerical('0.6','1/2','0','0')['correct'],False)

    def test_tolerance_boundaries(self):
        for answer,key,a,r,expected in [('10.1','10','.1','0',True),('10.1001','10','.1','0',False),
           ('102','100','.01','.02',True),('102.001','100','.01','.02',False),('.01','0','.01','0',True)]:
            self.assertIs(numerical(answer,key,a,r)['correct'],expected)
        self.assertEqual(numerical('1','1','-1','0')['outcome'],'evaluation_failed')

    def test_unsafe_numbers(self):
        for value in ['NaN','Infinity','1/0','2**4','__import__("os")','1e100000','1'*65,True,None,'1,2']:
            with self.subTest(value=value),self.assertRaises(ValueError):number(value)

    def test_numerical_reference(self):
        q=self.question();q.pop('options')
        item=validate_questions([q],1,'immediate')[0]
        self.assertIs(grade(item,'24.0')['correct'],True)
        self.assertEqual(grade(item,'eval(24)')['outcome'],'invalid')

    def test_partial_and_empty_metrics(self):
        values=metrics([{'correct':True}]+[{} for _ in range(4)])
        self.assertEqual(values['completion_percent'],20)
        self.assertEqual(values['accuracy_percent'],100)
        self.assertEqual(values['mastery_state'],'insufficient_evidence')
        self.assertIsNone(metrics([{}]*5)['accuracy_percent'])
        self.assertIsNone(metrics([{'correct':'false'}])['accuracy_percent'])

    def test_inconclusive_metrics(self):
        qs=[{'correct':True}]*3+[{'correct':False},{'student_answer':'answer','outcome':'inconclusive'}]
        m=metrics(qs);self.assertEqual(m['completion_percent'],100);self.assertEqual(m['accuracy_percent'],75)
        self.assertEqual(m['inconclusive'],1)
