# Contributing to RevOps Buddy

Thank you for your interest in contributing! This is primarily a portfolio project, but improvements and suggestions are welcome.

## How to Contribute

### Reporting Issues

If you find a bug or have a feature suggestion:

1. **Search existing issues** to avoid duplicates
2. **Open a new issue** with:
   - Clear title and description
   - Steps to reproduce (for bugs)
   - Expected vs actual behavior
   - Your environment (Python version, OS, etc.)

### Suggesting Features

Feature requests are welcome! Please include:
- **Use case**: What problem does this solve?
- **Proposed solution**: How should it work?
- **Alternatives considered**: What else did you think about?

### Code Contributions

While this is a portfolio project, I'm open to pull requests for:
- Bug fixes
- Documentation improvements
- Performance optimizations
- New agent types or tools (with clear use cases)

**Before submitting a PR:**
1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Test thoroughly
5. Commit with clear messages
6. Push to your fork
7. Open a Pull Request

### Code Style

- Follow PEP 8 for Python code
- Use type hints where appropriate
- Add docstrings to new functions/classes
- Keep line length < 100 characters
- Run `black` and `isort` before committing

### Testing

If adding new features:
- Add test cases to `tests/` directory
- Ensure existing tests still pass
- Test with mock data (don't require real pipelines)

## Development Setup

```bash
# Clone your fork
git clone https://github.com/your-username/revops-buddy.git
cd revops-buddy

# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests
pytest tests/

# Format code
black app/ demo/
isort app/ demo/
```

## Project Structure

```
revops-buddy/
├── app/
│   ├── agents/          # Agent implementations
│   ├── agent_new.py     # Main orchestration
│   ├── router.py        # Query routing
│   └── voice_loop.py    # Voice interface
├── demo/                # Demo scripts with mock data
├── docs/                # Documentation
├── tests/               # Test suite
└── README.md
```

## Questions?

Feel free to:
- Open an issue for questions
- Start a discussion in GitHub Discussions
- Reach out via email

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Thank you for helping improve RevOps Buddy! 🚀
