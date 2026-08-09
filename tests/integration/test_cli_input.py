from cli.ui import confirm_exit


def test_confirm_exit_requires_explicit_yes():
    assert confirm_exit(lambda _: "") is False
    assert confirm_exit(lambda _: "n") is False
    assert confirm_exit(lambda _: "yes") is True
    assert confirm_exit(lambda _: "Y") is True


def test_confirm_exit_treats_second_interrupt_as_cancel():
    def interrupt(_):
        raise KeyboardInterrupt

    assert confirm_exit(interrupt) is False
