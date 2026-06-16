# AI Assignment Auto-Grader

An AI-powered assignment grading system that automatically evaluates R, R Markdown, and Jupyter Notebook submissions using a rubric-based grading framework and Claude API.

## Project Overview

This project was built to automate the grading and feedback process for analytics coursework. The system monitors a submissions folder, parses student files, evaluates code and written responses against a predefined rubric and solution guide, and generates both individual feedback reports and a summary grading report.

The goal was to reduce repetitive manual grading work while improving grading consistency, feedback quality, and processing speed.

## Key Features

* Automatically grades `.R`, `.Rmd`, and `.ipynb` files
* Parses code cells, markdown cells, and written responses
* Uses Claude API to evaluate submissions against a structured rubric
* Supports solution-based logic comparison
* Generates detailed feedback reports for each student
* Creates a consolidated CSV grading report
* Monitors a submissions folder and grades new files automatically
* Supports single-file grading through a command-line argument

## Tech Stack

* Python
* Claude API
* Anthropic SDK
* watchdog
* python-dotenv
* JSON
* CSV
* R / R Markdown / Jupyter Notebook parsing

## Project Structure

```text
.
├── auto_grader.py
├── rubric.json
├── solution.ipynb
├── submissions/
│   └── student_submission.ipynb
├── grading_results/
│   ├── student_submission_feedback.txt
│   └── grading_report.csv
└── README.md
```

## How It Works

1. Student submissions are placed in the `submissions/` folder.
2. The system detects new `.R`, `.Rmd`, or `.ipynb` files.
3. Code and written responses are extracted from each file.
4. The rubric and solution guide are loaded.
5. Claude evaluates the submission and returns structured JSON results.
6. The system saves an individual feedback text file.
7. A summary row is added to `grading_report.csv`.

## Sample Output

The system generates an individual feedback file including:

* Total score and letter grade
* Part-level scores
* Item-level grading results
* Coding feedback
* Written-response feedback
* Overall feedback

It also creates a CSV report with:

* File name
* Total score
* Grade
* Evaluation timestamp
* Part-level scores
* Overall feedback summary

## Installation

```bash
pip install anthropic watchdog python-dotenv
```

## Environment Setup

Create a `.env` file and add your Anthropic API key:

```bash
ANTHROPIC_API_KEY=your_api_key_here
```

## Usage

To start folder monitoring:

```bash
python auto_grader.py
```

To grade a single file:

```bash
python auto_grader.py --grade-file submissions/student1.ipynb
```

## Business Impact

This project significantly reduced manual grading time by automating repetitive review tasks and generating structured feedback at scale. It improved consistency across submissions and allowed instructors to focus more on higher-level review, student support, and course improvement.

## Skills Demonstrated

* AI workflow automation
* Prompt engineering
* Python scripting
* File parsing
* API integration
* JSON processing
* Automated reporting
* Rubric-based evaluation
* Educational technology
* Process improvement

## Notes

This project was developed for an analytics coursework grading workflow. Student submissions and private grading data are not included in this repository.
