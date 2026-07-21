# Workspace Rules for AutoReel

## Version Management Rule
At the end of any task, feature implementation, or bug fix, you MUST update the project version (e.g., by creating a new `git tag` like `v1.0.1` or `v1.1.0` and pushing it). 

**CRITICAL CONSTRAINT:** You must ONLY do this when the codebase is completely done, stable, and verified (e.g., passing smoke tests, syntax checks, or dry runs). Never bump the version or push tags if there is ongoing work or untested code.

## Scratch Workspace Rule
You MUST NEVER write temporary test scripts, scratch files, or download temporary media directly into the root workspace directory, project packages, `tmp/`, `.gemini/`, or external locations like the Desktop. 
All temporary, investigatory, or one-off test files MUST be written exclusively to the `scratch/` directory. If you are writing a script just to test an API or debug a module, save it as `scratch/test_something.py`. This ensures no junk files are ever accidentally committed during a `git add .` command.
