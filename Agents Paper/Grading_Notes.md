# Assignment 5: Draft Agent Paper

**Due Date:** April 16, 2026 (Thursday, Week 12\) **Format:** PDF ([ICLR LaTeX template](https://github.com/ICLR/Master-Template/raw/master/iclr2026.zip)) **Page Length:** 3-4 pages (excluding references) **Submission:** Canvas (one per group)

## Overview

Write a draft paper documenting your agent implementation and evaluation. This paper presents your agent approach and analyzes its performance on your benchmark.

## Required Sections

Your agent paper should include:

### 1\. Introduction (\~0.75 pages)

* **Problem:** What challenge are you addressing with your agent?

* **Limitations:** Why are existing agents insufficient?

* **Your approach:** What is your agent’s key insight or novelty?

* **Results preview:** Briefly mention your main findings

* **Overview:** Roadmap of the paper

### 2\. Related Work (\~0.5 pages)

Discuss 4-6 related agent systems: \- **Similar agents:** Systems targeting similar problems \- **Technical approaches:** Agents using similar techniques \- **Comparison:** How does your agent differ?

Focus on agent systems, not benchmarks (those are in the benchmark paper).

### 3\. Agent Design (\~1.5 pages)

This is the core technical section. Include:

#### *Architecture Overview*

* High-level diagram of your agent architecture

* Key components and their roles

* Information flow between components

#### *Technical Approach*

Describe in detail: \- **Input processing:** How does your agent understand tasks? \- **Planning/reasoning:** How does it decide what to do? \- **Execution:** How does it perform actions? \- **Memory/state:** How does it maintain context? \- **Error handling:** How does it recover from failures?

#### *Implementation Details*

* What LLMs or models do you use?

* What prompting strategies do you employ?

* What tools or APIs does your agent access?

* What algorithms or techniques are key to your approach?

#### *Design Decisions*

* What alternative approaches did you consider?

* Why did you make the choices you made?

* What trade-offs did you navigate?

### 4\. Experimental Setup (\~0.5 pages)

Describe your evaluation:

#### *Benchmark*

* Briefly describe the benchmark you’re using (reference your benchmark paper)

* Note: Don’t repeat full benchmark details—summarize briefly

#### *Baselines*

* What agents are you comparing against?

* How did you implement or access them?

* Why are these appropriate comparisons?

#### *Evaluation Metrics*

* What metrics are you using?

* How are they computed?

#### *Experimental Details*

* How many trials per task?

* What parameters or settings did you use?

* Any data preprocessing or setup?

### 5\. Results (\~0.75 pages)

Present your experimental findings:

#### *Overall Performance*

* Table comparing your agent to baselines across key metrics

* Overall success rates, quality scores, efficiency metrics

#### *Performance by Task Type*

* How does performance vary across different types of tasks?

* What patterns emerge?

* Include graphs or charts showing performance breakdown

#### *Qualitative Analysis*

* Example successes: Show 1-2 examples where your agent excels

* Example failures: Show 1-2 examples where your agent struggles

* What do these examples reveal?

**Note for draft:** If you don’t have complete results yet, include whatever preliminary results you have and note what’s pending.

### 6\. Discussion (\~0.5 pages)

Analyze your results:

#### *What Works*

* Why does your agent perform well on certain tasks?

* What aspects of your design contribute to success?

#### *What Doesn’t Work*

* Where does your agent struggle?

* What are the failure modes?

* Why do these failures occur?

#### *Limitations*

* What can’t your agent handle?

* What assumptions does your approach rely on?

* When might your agent not be appropriate?

**Note:** Honest discussion of limitations is valued. Negative results are acceptable if well-analyzed.

### 7\. Conclusion (\~0.25 pages)

* **Summary:** Recap your agent approach and main findings

* **Contributions:** What does your work demonstrate?

* **Future work:** How could your agent be improved or extended?

### 8\. References

Include all cited papers and resources.

## Additional Materials

### Appendix (Optional, not counted toward page limit)

* Detailed prompts or templates

* Additional experimental results

* Ablation studies

* Error analysis

### Figures and Tables

Include relevant visualizations: \- **Required:** \- Agent architecture diagram \- Results table comparing to baselines \- **Suggested:** \- Performance by task type graph \- Example task execution trace \- Ablation study results \- Qualitative examples

## What Should Be Complete vs. Outline

**Must be complete:** \- Introduction \- Agent Design section with full technical details \- Experimental Setup

**Can be less polished:** \- Related Work (can have citation placeholders) \- Results (can have preliminary or partial results) \- Discussion (can be brief initial analysis) \- Conclusion

## “What if my agent doesn’t work well?”

**Negative results are acceptable and valuable** if you: \- Report honestly and thoroughly \- Analyze why performance is lower than expected \- Discuss what you learned from failures \- Propose how future work could address limitations

We grade on depth of analysis and understanding, not just on high performance numbers.

## Writing Tips

1. **Focus on your agent:** Don’t repeat benchmark details—reference the benchmark paper

2. **Be technical:** Provide enough detail that someone could reimplement your approach

3. **Show examples:** Include concrete examples of agent behavior

4. **Analyze deeply:** Don’t just report numbers—explain what they mean

5. **Be honest:** Acknowledge limitations and failures

## Grading Rubric (15 points)

* **Agent Design** (7pt): Is the agent approach clearly explained with sufficient technical detail?

* **Results** (4pt): Are results presented clearly with appropriate analysis?

* **Discussion** (2pt): Is there thoughtful analysis of what works and what doesn’t?

* **Clarity** (2pt): Is the paper well-written and well-organized?

## Notes

* This paper focuses solely on the **agent**—the benchmark is documented separately

* You’ll refine this for the final agent paper submission

* Integrate any feedback you receive on this draft