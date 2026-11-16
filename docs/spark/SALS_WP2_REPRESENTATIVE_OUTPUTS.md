# Authored WP2 teaching examples

These are deterministic representative outputs, not transcripts of live model responses.

## line3d

**Interpret the point and direction**

The point anchors the line; a nonzero direction vector fixes its orientation, not an endpoint.

$$P=(1, 2, 3),\qquad \mathbf{d}=(2, -1, 4)$$

Moving one parameter unit adds exactly the direction vector; negative values move the opposite way. This gives a geometric rule for generating every point on the line.

**Introduce one common parameter**

A line uses one displacement parameter: every coordinate belongs to the same point at the same value of t.

$$\mathbf{r}(t)=\mathbf{P}+t\mathbf{d}$$

$$x=1 + 2t,\quad y=2 - t,\quad z=3 + 4t$$

At t=0 the coordinates recover P. Independent parameters would describe a larger set, not this line. These parametric equations turn the geometric rule into coordinate equations.

**Isolate the parameter in each moving coordinate**

Subtract each starting coordinate, then divide by its nonzero direction component. These reversible operations preserve equality.

$$t=\frac{x-1}{2},\quad t=\frac{y-2}{-1},\quad t=\frac{z-3}{4}$$

Each ratio measures the same displacement parameter, not three different distances. We can therefore eliminate the common parameter by equating the ratios.

**Equate the parameter expressions**

The ratios are equal because they all equal the same t.

$$\frac{x-1}{2}=\frac{y-2}{-1}=\frac{z-3}{4}$$

The symmetric form describes exactly the points whose coordinate displacements share that parameter. Together with any fixed-coordinate equations, it answers the original line-equation problem.

**Check conditions and a point on the line**

Division by zero is undefined. A zero direction component instead fixes that coordinate; it must not appear as a zero denominator.

$$t=1\quad\Longrightarrow\quad (x,y,z)=(3,1,7)$$

Every moving-coordinate ratio is then 1; fixed coordinates stay equal to their starting values. This substitution checks both the algebra and the geometric interpretation.

## recursion

**Recursion: reduce a problem until a base case stops the calls.**

A function needs a stopping case and a smaller input on every recursive path. This is why factorial uses `fact(0) = 1` and `fact(n) = n * fact(n-1)` for nonnegative integers. The smaller argument makes progress toward termination; the multiplication combines the smaller answer with the current input.

**Trace a worked example: `fact(3)`.**

`fact(3) = 3 * fact(2)` reduces the input; `fact(2) = 2 * fact(1)` reduces it again. Then `fact(1) = 1 * fact(0)` and the base case returns 1. Calls unwind as `1 * 1 = 1`, `2 * 1 = 2`, `3 * 2 = 6`. Each return is used by the caller; merely reaching the base case does not make the whole answer 1. This example assumes nonnegative integer input.

## photosynthesis

**Photosynthesis connects matter and energy.**

Plants use light energy to build sugars from carbon dioxide and water; oxygen is released. Chlorophyll captures light, but light supplies energy rather than carbon atoms.

$$6CO_2+6H_2O\longrightarrow C_6H_{12}O_6+6O_2$$

Both sides contain 6 carbon atoms, 12 hydrogen atoms and 18 oxygen atoms, so the equation conserves matter. The sugar stores chemical energy; the carbon in plant biomass comes largely from carbon dioxide, not from soil. This is a simplified overall equation, not a description of every biochemical step.

**Photosynthesis connects matter and energy.**

Plants use light energy to build sugars from carbon dioxide and water; oxygen is released. Chlorophyll captures light, but light supplies energy rather than carbon atoms.

$$6CO_2+6H_2O\longrightarrow C_6H_{12}O_6+6O_2$$

Both sides contain 6 carbon atoms, 12 hydrogen atoms and 18 oxygen atoms, so the equation conserves matter. The sugar stores chemical energy; the carbon in plant biomass comes largely from carbon dioxide, not from soil. This is a simplified overall equation, not a description of every biochemical step.
