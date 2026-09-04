from deflare import CfSolver, Solver


def test_public_api_uses_solver_name():
    assert Solver is not None
    assert CfSolver is Solver
