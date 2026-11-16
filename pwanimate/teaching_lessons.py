"""Small auditable teaching exemplars. General topics use the existing answer model."""
import re
from fractions import Fraction
from dataclasses import dataclass
from pwanimate.assessment_validation import numerical


@dataclass(frozen=True)
class TeachingStep:
    operation: str
    validity: str
    expression: str
    meaning: str
    contribution: str

    def markdown(self):
        return f'**{self.operation}**\n\n{self.validity}\n\n{self.expression}\n\n{self.meaning} {self.contribution}'


def lesson_key(text):
    text = text.casefold()
    if any(word in text for word in ('fibonacci','binary search','tail recursion','tree traversal','calvin','c4 pathway','intersection','skew lines')):
        return 'general'
    if any(value in text for value in ('symmetric equation', 'parametric equation', 'direction vector', 'line in 3d', 'lines in 3d')):
        return 'line3d'
    if 'recursion' in text or 'recursive' in text:
        return 'recursion'
    if 'photosynthesis' in text:
        return 'photosynthesis'
    return 'general'


def line_data(text):
    number = r'([+-]?\d+(?:/\d+|\.\d+)?)'
    triple = r'\(\s*' + number + r'\s*,\s*' + number + r'\s*,\s*' + number + r'\s*\)'
    point = re.search(r'\bP\s*' + triple, text, re.I)
    direction = re.search(r'\bdirection(?:\s+vector)?\s*(?:is|=|:)?\s*' + triple, text, re.I)
    try:
        p = tuple(Fraction(v) for v in point.groups()) if point else (Fraction(1), Fraction(2), Fraction(3))
        d = tuple(Fraction(v) for v in direction.groups()) if direction else (Fraction(2), Fraction(-1), Fraction(4))
        if any(abs(v) > 10000 for v in (*p,*d)):
            raise ValueError('Example values exceed the supported range')
    except (ZeroDivisionError, ValueError):
        raise ValueError('Use finite numerical coordinates and a nonzero direction vector')
    return p,d


def line_steps(text):
    p,d = line_data(text)
    if not any(d):
        return [TeachingStep('Check the direction vector',
            'A direction must be nonzero; the zero vector specifies no direction.', '',
            'These data describe a point, not a unique line.', 'Choose a nonzero direction before deriving a line.')]
    variables = ['x','y','z']
    equations, ratios, fixed = [], [], []
    for variable, origin, direction in zip(variables,p,d):
        term = (' + ' if direction > 0 else ' - ') + ('' if abs(direction) == 1 else str(abs(direction))) + 't' if direction else ''
        equations.append(f'{variable}={origin}{term}')
        numerator = f'{variable}-{origin}' if origin >= 0 else f'{variable}+{abs(origin)}'
        if direction:
            ratios.append(r'\frac{' + numerator + '}{' + str(direction) + '}')
        else:
            fixed.append(f'{variable}={origin}')
    symmetric = '='.join(ratios) + ('=t' if len(ratios)==1 else '')
    if fixed:
        symmetric += r',\qquad ' + r',\ '.join(fixed)
    check = tuple(a+b for a,b in zip(p,d))
    return [TeachingStep('Interpret the point and direction',
        'The point anchors the line; a nonzero direction vector fixes its orientation, not an endpoint.',
        rf'$$P={tuple(str(v) for v in p)},\qquad \mathbf{{d}}={tuple(str(v) for v in d)}$$'.replace("'",''),
        'Moving one parameter unit adds exactly the direction vector; negative values move the opposite way.',
        'This gives a geometric rule for generating every point on the line.'),
      TeachingStep('Introduce one common parameter',
        'A line uses one displacement parameter: every coordinate belongs to the same point at the same value of t.',
        r'$$\mathbf{r}(t)=\mathbf{P}+t\mathbf{d}$$' + '\n\n$$' + r',\quad '.join(equations) + '$$',
        'At t=0 the coordinates recover P. Independent parameters would describe a larger set, not this line.',
        'These parametric equations turn the geometric rule into coordinate equations.'),
      TeachingStep('Isolate the parameter in each moving coordinate',
        'Subtract each starting coordinate, then divide by its nonzero direction component. These reversible operations preserve equality.',
        '$$' + r',\quad '.join('t='+r for r in ratios) + '$$',
        'Each ratio measures the same displacement parameter, not three different distances.',
        'We can therefore eliminate the common parameter by equating the ratios.'),
      TeachingStep('Equate the parameter expressions',
        'The ratios are equal because they all equal the same t.', '$$'+symmetric+'$$',
        'The symmetric form describes exactly the points whose coordinate displacements share that parameter.',
        'Together with any fixed-coordinate equations, it answers the original line-equation problem.'),
      TeachingStep('Check conditions and a point on the line',
        'Division by zero is undefined. A zero direction component instead fixes that coordinate; it must not appear as a zero denominator.',
        r'$$t=1\quad\Longrightarrow\quad (x,y,z)=(' + ','.join(str(v) for v in check) + ')$$',
        'Every moving-coordinate ratio is then 1; fixed coordinates stay equal to their starting values.',
        'This substitution checks both the algebra and the geometric interpretation.')]


def content(key, phase, objective, *, deep=False, simpler=False):
    if key == 'line3d':
        steps = line_steps(objective)
        selected = steps[:2] if phase in {'DIAGNOSE','EXPLAIN'} else steps[2:]
        text = '\n\n'.join(step.markdown() for step in selected)
        if deep:
            text += '\n\n**Another viewpoint:** scaling a nonzero direction vector rescales t but leaves the line unchanged. The parameter is a coordinate along the line, not necessarily physical time.'
        return text
    if key == 'recursion':
        if phase in {'DIAGNOSE','EXPLAIN'}:
            return '**Recursion: reduce a problem until a base case stops the calls.**\n\nA function needs a stopping case and a smaller input on every recursive path. This is why factorial uses `fact(0) = 1` and `fact(n) = n * fact(n-1)` for nonnegative integers. The smaller argument makes progress toward termination; the multiplication combines the smaller answer with the current input.'
        return '**Trace a worked example: `fact(3)`.**\n\n`fact(3) = 3 * fact(2)` reduces the input; `fact(2) = 2 * fact(1)` reduces it again. Then `fact(1) = 1 * fact(0)` and the base case returns 1. Calls unwind as `1 * 1 = 1`, `2 * 1 = 2`, `3 * 2 = 6`. Each return is used by the caller; merely reaching the base case does not make the whole answer 1. This example assumes nonnegative integer input.'
    if key == 'photosynthesis':
        return '**Photosynthesis connects matter and energy.**\n\nPlants use light energy to build sugars from carbon dioxide and water; oxygen is released. Chlorophyll captures light, but light supplies energy rather than carbon atoms.\n\n$$6CO_2+6H_2O\\longrightarrow C_6H_{12}O_6+6O_2$$\n\nBoth sides contain 6 carbon atoms, 12 hydrogen atoms and 18 oxygen atoms, so the equation conserves matter. The sugar stores chemical energy; the carbon in plant biomass comes largely from carbon dioxide, not from soil. This is a simplified overall equation, not a description of every biochemical step.'
    return ''


def exercise(key, objective, round_number, difficulty):
    if key == 'line3d':
        p,d = line_data(objective)
        if not any(d):
            return {}
        t = round_number + (1 if difficulty == 1 else 2)
        axis = next((i for i in (1,0,2) if d[i]), 0)
        name = ('x','y','z')[axis]
        return {'prompt': f'For the line through P({",".join(map(str,p))}) with direction vector ({",".join(map(str,d))}), what is {name} when t={t}? Explain your substitution if you like, or submit the numerical value.',
            'expected':str(p[axis]+t*d[axis]), 'type':'numeric', 'formula':f'{name}={p[axis]}+({d[axis]})t',
            'substitution':f'{name}={p[axis]}+({d[axis]})({t})', 'identity':f'line:{axis}:{t}'}
    if key == 'recursion':
        n = round_number + 3
        if n > 8:
            return {}
        from math import factorial
        return {'prompt':f'Using fact(0)=1 and fact(n)=n*fact(n-1), what does fact({n}) return? Submit its numerical value.',
            'expected':str(factorial(n)), 'type':'numeric', 'formula':'fact(n)=n*fact(n-1), fact(0)=1',
            'substitution':f'Expand fact({n}) into {n} * fact({n-1}) and continue to the base case.', 'identity':f'factorial:{n}'}
    if key == 'photosynthesis':
        variants = [('Which substance supplies the carbon used to build sugars in photosynthesis?', ['Carbon dioxide','Oxygen','Soil minerals'], 'a'),
            ('Which option is the energy input for photosynthesis?', ['Soil carbon','Light','Oxygen'], 'b'),
            ('Which gas is released in the simplified overall photosynthesis reaction?', ['Oxygen','Nitrogen','Carbon dioxide'], 'a')]
        if round_number > len(variants):
            return {}
        prompt, labels, expected = variants[round_number-1]
        return {'prompt':prompt, 'options':[{'id':chr(97+i),'label':label} for i,label in enumerate(labels)],
            'expected':expected, 'type':'choice', 'formula':'Identify reactants, products and the energy input separately.',
            'substitution':'Carbon dioxide and water are inputs; sugar and oxygen are outputs; light supplies energy.', 'identity':f'photosynthesis:{round_number}'}
    return {}


def evaluate_exercise(item, answer):
    if item.get('type') == 'numeric':
        return numerical(str(answer), item['expected'], '0', '0')
    if item.get('type') == 'choice':
        options = item.get('options', [])
        matches = [o['id'] for o in options if str(answer).strip().casefold() in {o['id'], o['label'].casefold()}]
        if len(matches) == 1:
            return {'outcome':'correct' if matches[0] == item['expected'] else 'incorrect', 'correct':matches[0] == item['expected']}
    return {'outcome':'inconclusive','correct':None}


def alternative(key, objective):
    if key == 'line3d':
        p,d=line_data(objective)
        return ('**Think of a shared slider.** Start at P'+str(tuple(map(str,p))).replace("'",'')+
            '. When the slider moves by one unit, all three coordinates change together by '+str(tuple(map(str,d))).replace("'",'')+
            '. You cannot set the x-slider and y-slider separately: there is only one slider t. '
            'A zero direction component means that coordinate never moves. The ratios recover the slider value from a point, so they must agree.\n\n'+content(key,'EXPLAIN',objective))
    if key == 'recursion':
        return ('**Think of unfinished boxes on a stack.** Each recursive call leaves a multiplication waiting in its own box. '
            'A smaller nonnegative input eventually reaches the base case, which supplies the first answer. '
            'Opening the boxes in reverse order combines those answers. A base case alone is insufficient if a call never makes the input smaller.\n\n'+content(key,'DEMONSTRATE',objective))
    if key == 'photosynthesis':
        return ('**Separate building materials from energy.** Carbon dioxide supplies carbon atoms; light supplies energy. '
            'A building site needs both materials and energy, but energy does not substitute for the materials. '
            'In the same way, light is not the source of the carbon atoms in sugar.\n\n'+content(key,'EXPLAIN',objective))
    return ''


def quick_content(key, objective):
    if key == 'line3d':
        steps=line_steps(objective)
        if len(steps)==1: return steps[0].markdown()
        return ('The point anchors the line and the nonzero direction vector tells how its coordinates change together. '
            'Vector addition gives one common parameter t:\n\n'+steps[1].expression+
            '\n\nSubtract each starting coordinate and divide by its nonzero direction component. '
            'These reversible operations recover the same t from every moving coordinate, so the ratios are equal:\n\n'+steps[3].expression+
            '\n\nA zero direction component instead fixes that coordinate; do not divide by zero. '
            'This symmetric form describes the same line. '+steps[4].expression)
    if key == 'recursion':
        return 'Recursion solves a problem using smaller instances of itself. A base case stops the calls; each recursive path must approach it. For nonnegative integers, `fact(0)=1` and `fact(n)=n*fact(n-1)`, so `fact(3)=3*2*1*1=6`. The returned values combine as the calls unwind.'
    if key == 'photosynthesis':
        return 'Photosynthesis uses light energy to build sugars from carbon dioxide and water, releasing oxygen. Carbon dioxide supplies the carbon; light supplies energy. The simplified overall reaction conserves atoms: $$6CO_2+6H_2O\\longrightarrow C_6H_{12}O_6+6O_2$$'
    return ''
