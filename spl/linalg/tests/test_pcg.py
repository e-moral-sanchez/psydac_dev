import numpy as np
import pytest

#===============================================================================
@pytest.mark.parametrize( 'n', [8, 16] )
@pytest.mark.parametrize( 'p', [2, 3] )

def test_pcg(n, p):
    """
    Test preconditioned Conjugate Gradient algorithm on tridiagonal linear system.

    Parameters
    ----------
    n : int
        Dimension of linear system (number of rows = number of columns).

    """
    from spl.linalg.solvers import pcg
    from spl.linalg.stencil import StencilVectorSpace, StencilMatrix, StencilVector
    #---------------------------------------------------------------------------
    # PARAMETERS
    #---------------------------------------------------------------------------

    # ... Vector Spaces
    V = StencilVectorSpace([n], [p], [False])
    e = V.ends[0]
    s = V.starts[0]

    # Build banded matrix with 2p+1 diagonals: must be symmetric and positive definite
    # Here we assign value 2*p on main diagonal and -1 on other diagonals
    A = StencilMatrix(V, V)
    A[:,-p:0  ] = -1
    A[:, 0:1  ] = 2*p
    A[:, 1:p+1] = -1
    A.remove_spurious_entries()

    # Build exact solution
    xe = StencilVector(V)
    xe[s:e+1] = np.random.random(e+1-s)

    # Tolerance for success: L2-norm of error in solution
    tol = 1e-5

    #---------------------------------------------------------------------------
    # TEST
    #---------------------------------------------------------------------------

    # Title
    print()
    print("="*80)
    print("SERIAL TEST: solve linear system A*x = b using preconditioned conjugate gradient")
    print("="*80)
    print()

    # Manufacture right-hand-side vector from exact solution
    b = A.dot(xe)

    # Solve linear system using PCG
    x, info = pcg( A, b, tol=1e-12 )

    # Verify correctness of calculation: L2-norm of error
    err = x-xe
    err_norm = np.linalg.norm(err.toarray())

    #---------------------------------------------------------------------------
    # TERMINAL OUTPUT
    #---------------------------------------------------------------------------

    print()
    print( 'A  =', A.toarray(), sep='\n' )
    print( 'b  =', b.toarray())
    print( 'x  =', x.toarray())
    print( 'xe =', xe.toarray())
    print( 'info =', info )
    print()

    print( "-"*40 )
    print( "L2-norm of error in solution = {:.2e}".format(err_norm))
    if err_norm < tol:
        print( "PASSED" )
    else:
        print( "FAIL" )
    print( "-"*40 )

    #---------------------------------------------------------------------------
    # PYTEST
    #---------------------------------------------------------------------------
    assert err_norm < tol
