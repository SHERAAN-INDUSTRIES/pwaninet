"""Semantic rubric checks: algebraic equivalence, termination, conserved matter.

These test authored representative outputs. They do not claim that arbitrary
live model responses have been evaluated by a human instructor.
"""
from fractions import Fraction
from math import factorial
from django.test import SimpleTestCase
from pwanimate import teaching_lessons as lessons
from tests.sals_03_wp2.test_teaching import LINE


class TeachingQualityTests(SimpleTestCase):
    def test_math_rubric_every_step_has_operation_validity_meaning_and_goal(self):
        steps=lessons.line_steps(LINE)
        self.assertEqual(len(steps),5)
        for step in steps:
            self.assertTrue(all((step.operation,step.validity,step.expression,step.meaning,step.contribution)))
        # Inspect the displayed parametric equations as executable affine maps.
        p,d=lessons.line_data(LINE)
        self.assertEqual(p,(1,2,3)); self.assertEqual(d,(2,-1,4))
        for t in [Fraction(-3),Fraction(-1,2),Fraction(0),Fraction(1),Fraction(7,3)]:
            point=[a+t*b for a,b in zip(p,d)]
            self.assertEqual([(x-a)/b for x,a,b in zip(point,p,d)],[t]*3)
        self.assertIn(r'\frac{x-1}{2}=\frac{y-2}{-1}=\frac{z-3}{4}',steps[3].expression)
        self.assertIn('(3,1,7)',steps[4].expression)
        # Reconstruct P+t*d from each displayed coordinate equation.
        equations=steps[1].expression.split('$$')[-2].split(r',\quad ')
        for eq,a,b in zip(equations,p,d):
            expr=eq.split('=')[1].replace('t','*t')
            # Coefficient 1 has implicit t, normalize leading unary sign.
            if expr.endswith(' *t'): expr=expr.replace(' *t',' 1*t')
            if expr.endswith(' - *t'): expr=expr.replace(' - *t',' - 1*t')
            self.assertEqual(eval(expr,{'__builtins__':{}},{'t':2}),a+2*b)

    def test_zero_component_rubric_never_divides_by_zero(self):
        objective='Symmetric equations through P(1,2,3) with direction vector (0,-1,0)'
        p,d=lessons.line_data(objective)
        steps=lessons.line_steps(objective)
        self.assertNotIn('}{0}',steps[3].expression)
        self.assertIn('x=1',steps[3].expression); self.assertIn('z=3',steps[3].expression)
        for t in [-2,0,4]:
            x,y,z=[a+t*b for a,b in zip(p,d)]
            self.assertEqual((x,z),(1,3)); self.assertEqual((y-2)/-1,t)
        zero=lessons.line_steps('direction vector (0,0,0)')
        self.assertEqual(len(zero),1); self.assertEqual(zero[0].expression,'')

    def test_programming_rubric_new_attempts_and_terminating_trace(self):
        def trace(n):
            return [n]+trace(n-1) if n else [0]
        for round_number in range(1,6):
            exercise=lessons.exercise('recursion','Explain recursion',round_number,1)
            n=round_number+3
            values=trace(n)
            self.assertEqual(values[-1],0)
            self.assertTrue(all(a>b for a,b in zip(values,values[1:])))
            result=1
            for value in reversed(values[:-1]): result*=value
            self.assertEqual(result,factorial(n))
            self.assertEqual(exercise['expected'],str(result))
            self.assertNotEqual(n,3)  # worked example is not an independent new task
        self.assertFalse(lessons.exercise('recursion','',6,1))

    def test_theory_rubric_conserves_atoms_and_separates_energy(self):
        # Stoichiometric rubric for the representative equation actually shown.
        reactants={'C':6,'H':6*2,'O':6*2+6}
        products={'C':6,'H':12,'O':6+6*2}
        self.assertEqual(reactants,products)
        output=lessons.content('photosynthesis','EXPLAIN','')
        self.assertIn(r'6CO_2+6H_2O\longrightarrow C_6H_{12}O_6+6O_2',output)
        cases=[('Carbon dioxide',True),('Oxygen',False),('Light',None)]
        item=lessons.exercise('photosynthesis','',1,1)
        for answer,expected in cases:
            self.assertIs(lessons.evaluate_exercise(item,answer)['correct'],expected)
        self.assertEqual(lessons.exercise('photosynthesis','',2,1)['expected'],'b')

    def test_specialized_requests_use_model_instead_of_wrong_authored_example(self):
        for query in ['Explain Fibonacci recursion','Explain the Calvin cycle in photosynthesis',
                      'Find intersection of lines in 3D']:
            self.assertEqual(lessons.lesson_key(query),'general')

    def test_formal_partial_hints_do_not_include_reviewed_final_answers(self):
        from pwanimate.services.teaching import TeachingService
        from pwanimate.assessment_validation import REFERENCES
        for key,ref in REFERENCES.items():
            partial=TeachingService._formal_partial(key)
            self.assertNotIn(ref['expected'],partial)
            self.assertNotEqual(partial,TeachingService._formal_explanation(key))
