# coding: utf-8
#
# author by Martin Campos Pinto, campos@ljll.math.upmc.fr

"""
Basic module that provides local smoothing projection operators in spline spaces

"""

import numpy as np

from scipy.integrate import quad
from scipy.interpolate import BSpline
from scipy.interpolate import splev
from scipy.special import comb
from scipy.special import factorial
from scipy.integrate import quadrature
# from scipy.special.orthogonal import p_roots
from scipy.sparse import csr_matrix, csc_matrix, coo_matrix
from scipy.sparse.linalg import splu

import matplotlib
matplotlib.use('Agg')   # backend configuration: high quality PNG images using the Anti-Grain Geometry engine
import matplotlib.pyplot as plt

from spl.core.interface import make_open_knots
from spl.core.interface import construct_grid_from_knots
from spl.core.interface import construct_quadrature_grid
from spl.utilities.quadratures import gauss_legendre

from spl.utilities.integrate import Integral
from spl.utilities.integrate import Interpolation
from spl.utilities.integrate import Contribution

from spl.feec.utilities import interpolation_matrices
from spl.feec.utilities import get_tck
from spl.feec.utilities import mass_matrices
from spl.feec.utilities import scaling_matrix


# import os.path
# import spl.core as core
# print ('writing path : ')
# print (os.path.abspath(core.__file__))
# exit()

# ...
def solve(M, x):
    """Solve y:= Mx using SuperLU."""
    M = csc_matrix(M)
    M_op = splu(M)
    return M_op.solve(x)
# ...

class LocalProjectionClass:
    """
    trial class for local spline projection operators,
    uses a smooth spline space
    and a discontinuous (pw smooth) spline space made of several subdomains
        """

    def __init__(self,
                 p,
                 m,
                 N_cells_sub=None,
                 N_subdomains=1,
                 watch_my_steps=False,
                 n_checks=5,
                 use_macro_elem_duals=False,
                 ):
        """
        p: int
            spline degree

        m: int
            degree of the moments preserved by the smoothing operator

        N_cells_sub: int
            number of cells on each subdomain, must be a multiple of m+p+1 the macro-elements size

        N_subdomains: int
            number of subdomains
        """
        if not (isinstance(p, int) and isinstance(m, int) and isinstance(N_cells_sub, int) and isinstance(N_subdomains, int)):
            raise TypeError('Wrong type for p, m, N_cells_sub and/or N_subdomains: must be int')

        # degree of the moments preserved by the smoothing operator
        self._m = m

        self._p = p
        self._N_subdomains = N_subdomains
        self._N_cells_sub = N_cells_sub
        self._N_cells = N_subdomains * N_cells_sub
        self._x_min = 0
        self._x_max = 1
        self._h = (self._x_max-self._x_min)*(1./self._N_cells)
        self._H = (self._x_max-self._x_min)*(1./self._N_subdomains)

        # Macro cells:
        self._M_p = m+p+1   # nb of cells in a macro-cell
        if use_macro_elem_duals and (not np.mod(N_cells_sub, self._M_p) == 0):
            raise ValueError('Wrong value for N_cells_sub, must be a multiple of m+p+1 for a macro-element dual basis')
        self._N_macro_cells = self._N_cells // self._M_p

        # number of spline basis functions:
        self._n = self._N_cells + p  # nb of basis functions phi_i in the smooth space V_h
        self._sub_n = self._N_cells_sub + p   # nb of basis functions tilde_phi_{s,i} in each subdomain (s)
        self._tilde_n = self._N_subdomains * self._sub_n  # nb of basis functions in the disc space tilde_V_h

        # spline coefs
        self.coefs = np.zeros(self._n, dtype=np.double)
        self.tilde_coefs = np.zeros((self._N_subdomains, self._sub_n), dtype=np.double)

        # open knot vectors
        self._Xi = self._x_min + (self._x_max - self._x_min) * make_open_knots(self._p, self._n)
        self._tilde_x_min = [ self._x_min + s*self._H for s in range(self._N_subdomains)]
        self._tilde_x_max = [ self._x_min + (s+1)*self._H for s in range(self._N_subdomains)]
        Xi_sub = make_open_knots(self._p, self._sub_n)
        self._tilde_Xi = [self._tilde_x_min[s] + self._H * Xi_sub for s in range(self._N_subdomains)]
        print("self._tilde_Xi[0] : ", self._tilde_Xi[0])
        print("self._tilde_Xi[1] : ", self._tilde_Xi[1])
        
        self.grid = construct_grid_from_knots(p, self._n, self._Xi)
        assert len(self.grid) == self._N_cells + 1

        # flag
        self._use_macro_elem_duals = use_macro_elem_duals

        # Duality products
        # self.duality_prods = np.zeros((self._n, self._n, self._N_cells), dtype=np.double)

        self._psi_P_coefs = [np.zeros((self._p + 1, self._p + 1)) for k in range(self._N_cells)]  # todo: try sequences of None
        self._psi_M_aux_coefs = [None for ell in range(self._N_macro_cells)]
        # self._psi_M_aux_coefs = [np.zeros((self._m + 1, self._m + 1)) for ell in range(self._N_macro_cells)]
        self._left_correction_products_psi_M_aux = [np.zeros((self._m+1, self._p)) for ell in range(self._N_macro_cells)]
        self._right_correction_products_psi_M_aux = [np.zeros((self._m+1, self._p)) for ell in range(self._N_macro_cells)]

        # -- construction of the P dual basis ---
        print("building the P dual basis")

        # change-of-basis matrices for each I_k
        temp_matrix = np.zeros((self._p + 1, self._p + 1))
        for k in range(self._N_cells):
            temp_matrix[:,:] = 0
            for a in range(self._p + 1):
                bern_ak = lambda x: self._bernstein_P(a, k, x)
                for b in range(self._p + 1):
                    j = k + b
                    phi_jk = lambda x: self._phi(j, x)  # could be phi_pieces(j,k,x) but we only evaluate it on I_k so its the same
                    temp_matrix[a, b] = _my_L2_prod(bern_ak, phi_jk, xmin=self._Xi[k+p], xmax=self._Xi[k+p+1])
            self._psi_P_coefs[k] = np.linalg.inv(temp_matrix)

        # alpha coefficients
        self._alpha = np.zeros((self._n, self._N_cells))
        int_phi = np.zeros(self._n)
        for i in range(self._n):
            for a in range(self._p+1):
                int_phi[i] += quadrature(
                    lambda x: self._phi(i, x),
                    self._Xi[i+a],
                    self._Xi[i+a+1],
                    maxiter=self._p+1,
                    vec_func=False,
                )[0]
            # print("i = ", i, "  --  int_phi[i] = ", int_phi[i])

        for k in range(self._N_cells):
            for a in range(self._p+1):
                i = k + a
                assert i < self._n
                self._alpha[i,k] = quadrature(
                    lambda x: self._phi(i, x),
                    self._Xi[k+p],
                    self._Xi[k+p+1],
                    maxiter=self._p+1,
                    vec_func=False,
                )[0]/int_phi[i]

        if self._use_macro_elem_duals:
            M = self._M_p
            m = self._m

            # change-of-basis coefs for the macro element dual functions
            print("building the M dual basis")
            temp_matrix = np.zeros((m + 1, m + 1))
            for ell in range(self._N_macro_cells):
                temp_matrix[:,:] = 0
                for a in range(m + 1):
                    bern_a_ell = lambda x: self._bernstein_M(a, ell, x) # local degree m
                    for b in range(m + 1):
                        j = self._global_index_of_macro_element_dof(ell,b)
                        phi_j = lambda x: self._phi(j, x)  # local degree p
                        for k in range(ell*M, (ell+1)*M):
                            temp_matrix[a, b] += quadrature(
                                lambda x: bern_a_ell(x) * phi_j(x),
                                self._Xi[k + p],
                                self._Xi[k+1 + p],
                                maxiter=m+p+1,
                                vec_func=False,
                            )[0]
                self._psi_M_aux_coefs[ell] = np.linalg.inv(temp_matrix)

            if 0:
                print("check -- MM ")
                grid = construct_grid_from_knots(self._p, self._n, self._Xi)
                ell = 0
                coef_check = np.zeros((m+1,m+1))
                for a in range(m + 1):
                    i = self._global_index_of_macro_element_dof(ell, a)
                    for b in range(m + 1):
                        j = self._global_index_of_macro_element_dof(ell,b)
                        coef_check[a,b] = _my_L2_prod(
                            lambda x:self._psi_M_aux(i, x),
                            lambda x:self._phi(j, x),
                            sing_points=grid,
                        )
                print(coef_check)
                print('check done -- 847876474')
                exit()


            # correction coefs for the macro element dual functions
            print("computing correction coefs for the M dual basis")
            for ell in range(self._N_macro_cells):
                for a in range(m + 1):
                    i = self._global_index_of_macro_element_dof(ell, a)
                    psi_M_aux_i = lambda x: self._psi_M_aux(i, x)
                    for b in range(p):

                        # correction terms to enforce duality with duals of left and right macro-vertices:
                        j_left = self._global_index_of_macro_vertex_dof(ell, b)
                        j_right = self._global_index_of_macro_vertex_dof(ell+1, b)
                        phi_j_left =  lambda x: self._phi(j_left, x)
                        phi_j_right =  lambda x: self._phi(j_right, x)
                        temp_val_left = 0
                        temp_val_right = 0
                        for k in range(ell*M, (ell+1)*M):
                            temp_val_left += quadrature(
                                lambda x: psi_M_aux_i(x) * phi_j_left(x),
                                self._Xi[k + p],
                                self._Xi[k+1 + p],
                                maxiter=m+p+1,
                                vec_func=False,
                            )[0]
                            temp_val_right += quadrature(
                                lambda x: psi_M_aux_i(x) * phi_j_right(x),
                                self._Xi[k + p],
                                self._Xi[k+1 + p],
                                maxiter=m+p+1,
                                vec_func=False,
                            )[0]
                        self._left_correction_products_psi_M_aux[ell][a,b] = temp_val_left
                        self._right_correction_products_psi_M_aux[ell][a,b] = temp_val_right
                        # self._left_correction_products_psi_M_aux[ell][a,b] = _my_L2_prod(
                        #     psi_M_aux_i,
                        #     phi_j_left,
                        #     xmin=self._Xi[ell*M + p],
                        #     xmax=self._Xi[(ell+1)*M + p]
                        # )
                        # self._right_correction_products_psi_M_aux[ell][a,b] = _my_L2_prod(
                        #     psi_M_aux_i,
                        #     phi_j_right,
                        #     xmin=self._Xi[ell*M + p],
                        #     xmax=self._Xi[(ell+1)*M + p]
                        # )

        print("Ok, construction done, n_dofs (smooth space) = ", self._n)

    @property
    def N_cells(self):
        return self._N_cells

    # -- indices of macro elements, vertices and associated dofs --

    def dof_index_is_macro_vertex(self, i):
        return np.mod(i,self._M_p) < self._p

    def dof_index_is_macro_element(self, i):
        return not self.dof_index_is_macro_vertex(i)

    def macro_vertex_index_of_dof(self, i):
        assert self.dof_index_is_macro_vertex(i)
        ell = i // self._M_p
        assert 0 <= i - ell * self._M_p < self._p
        return ell

    def macro_element_index_of_dof(self, i):
        assert self.dof_index_is_macro_element(i)
        ell = i // self._M_p
        assert self._p <= i - ell * self._M_p < self._p + self._m + 1
        return ell

    def dof_indices_of_macro_vertex(self, ell):
        return [ell*self._M_p + a for a in range(self._p)]

    def dof_indices_of_macro_element(self, ell):
        return [ell*self._M_p + self._p + a for a in range(self._m+1)]

    def _local_index_of_macro_vertex_dof(self, i, ell):
        assert 0 <= i < self._n
        assert 0 <= ell <= self._N_macro_cells
        a = i - ell*self._M_p
        assert 0 <= a < self._p
        return a

    def _local_index_of_macro_element_dof(self, i, ell):
        assert 0 <= i < self._n
        assert 0 <= ell < self._N_macro_cells
        a = i - ell*self._M_p - self._p
        assert 0 <= a <= self._m
        return a

    def _global_index_of_macro_vertex_dof(self, ell, a):
        assert 0 <= ell <= self._N_macro_cells
        assert 0 <= a < self._p
        i = ell*self._M_p + a
        assert 0 <= i < self._n
        return i

    def _global_index_of_macro_element_dof(self, ell, a):
        assert 0 <= ell < self._N_macro_cells
        assert 0 <= a <= self._m
        i = ell*self._M_p + self._p + a
        assert 0 <= i < self._n
        return i

    def _bernstein_P(self, a, k, x):
        """
        a-th Bernstein polynomial of degree p on the interval I_k = [t_{k+p},t_{k+p+1}] -- else, 0
        """
        p = self._p
        assert a in range(p+1)
        t0 = self._Xi[k+p]
        t1 = self._Xi[k+p+1]
        if t0 <= x <= t1:
            t = (x-t0)/(t1-t0)
            return comb(p, a) * t**a * (1 - t)**(p - a)
        else:
            return 0

    def _bernstein_M(self, a, ell, x):
        """
        a-th Bernstein polynomial of degree m (the degree of preserved moments)
        on the macro-element hat I_k = [t_{ell*M+p},t_{(ell+1)*M+p}] -- else, 0
        """
        p = self._p
        m = self._m
        assert a in range(m+1)
        t0 = self._Xi[ell*self._M_p+p]    # todo: would be clearer with grid[ell*self._M_p] ...
        t1 = self._Xi[(ell+1)*self._M_p+p]
        if t0 <= x <= t1:
            t = (x-t0)/(t1-t0)
            return comb(m, a) * t**a * (1 - t)**(m - a)
        else:
            return 0

    def _phi(self, i, x):
        """
        basis functions for the smooth space:
        B-spline phi_i = B_i^p defined by the knots xi_i, ... , xi_{i+p+1}
        """
        assert i in range(self._n)
        p = self._p
        val = 0
        if self._Xi[i] <= x < self._Xi[i+p+1]:
            t = self._Xi[i:i+p+2]
            b = BSpline.basis_element(t)
            val = b(x)
        return val

    def _phi_pieces(self, i, k, x):
        """
        polynomial pieces of the B-splines on the smooth space (\varphi_{i,k} in my notes)
        defined as the restriction of the B-spline phi_i = B_i^p on the interval I_k = [t_{k+p},t_{k+p+1}]
        Note:
            since phi_i is supported on [t_i,t_{i+p+1}], this piece is zero unless k <= i <= k+p
            moreover for i = k, .. k+p they span a basis of P_p(I_k)
        """
        assert i in range(self._n)
        p = self._p
        val = 0
        if 0 <= k < self._N_cells and k <= i <= k+p and self._Xi[k+p] <= x < self._Xi[k+p+1]:
            val = self._phi(i, x)
        return val

    def _tilde_phi(self, x, i=None, s=None, g=None):
        """
        basis functions for the discontinuous space:
        B-spline B_i^p on the subdomain s, ie defined by the knots xi^s_i, ... xi^s_{i+p+1}
        alternatively, may be called with global index g = i + s*self._sub_n
        """
        if g is not None:
            assert s is None
            assert i is None
            assert 0 <= g < self._tilde_n
            s = g // self._sub_n
            i = np.mod(g, self._sub_n)
            assert g == self.index_tilde_dof(s,i)
        assert 0 <= s < self._N_subdomains
        assert 0 <= i < self._sub_n
        p = self._p
        val = 0
        if self._tilde_Xi[s][i] <= x < self._tilde_Xi[s][i+p+1]:
            t = self._tilde_Xi[s][i:i+p+2]
            b = BSpline.basis_element(t)
            val = b(x)
        return val

    def _psi_P_pieces(self, i, k, x):
        """
        local duals to the _phi_pieces, computed using Bernstein basis polynomials
        """
        assert i in range(self._n)
        p = self._p
        val = 0
        if 0 <= k < self._N_cells and k <= i <= k+p and self._Xi[k+p] <= x < self._Xi[k+p+1]:
            a = i - k
            for b in range(p+1):
                val += self._psi_P_coefs[k][a,b] * self._bernstein_P(b,k,x)
        return val

    def _psi_P(self, i, x):
        """
        duals to the _phi B-splines, of kind P
        """
        p = self._p
        val = 0
        if self._Xi[i] <= x < self._Xi[i+p+1]:
            # x is in one cell I_k = [t_{k+p},t_{k+p+1}] with i <= k+p <= i+p
            for a in range(p+1):
                k = i - a
                if 0 <= k < self._N_cells and self._Xi[k+p] <= x < self._Xi[k+p+1]:
                    val = self._alpha[i,k] * self._psi_P_pieces(i,k,x)
        return val

    # --  perfect splines and dual functions of de Boor.
    #
    #  formulas derived from
    #  Dornisch, W., Stöckler, J., & Müller, R. (2017).
    #  Dual and approximate dual basis functions for B-splines and NURBS – 
    #  Comparison and application for an efficient coupling of patches with the isogeometric mortar method.
    #  Computer Methods in Applied Mechanics and Engineering, 316, 449–496.
    #  http://doi.org/10.1016/j.cma.2016.07.038 ---

    def _sing_points_perfect_spline(self):
        p = self._p
        if p == 1:
            return [-1,0,1]
        elif p == 2:
            return [-1, -0.5, 0.5, 1]
        elif p == 3:
            sr2_2 = np.sqrt(2)/2
            return [-1, -sr2_2, 0, sr2_2, 1]

    def der_perfect_spline(self, z, r):
        """
        evaluates the r-th order derivative of the perfect spline of degree p
        :param z: evaluation point
        :param r: derivative order, 0 <= r <= p
        :return: D^r B_p^*(z)
        """
        p = self._p
        assert 0 <= r <= p
        s = p-r
        factor = np.prod(range(s+1,p+1))
        if not -1 <= z <= 1:
            return 0

        if p == 1:
            return trunc_pow(z+1,s) - 2*trunc_pow(z,s)
        elif p == 2:
            return factor * (
                2*trunc_pow(z+1,s) - 4*trunc_pow(z+0.5,s) + 4*trunc_pow(z-0.5,s)
            )
        elif p == 3:
            sr2_2 = np.sqrt(2)/2
            return factor * (
                4*trunc_pow(z+1,s) - 8*trunc_pow(z+sr2_2,s) + 8*trunc_pow(z,s) - 8*trunc_pow(z-sr2_2,s)
            )
        else:
            raise ValueError("der_perfect_spline is only implemented for p <= 3")

    def der_transition_function(self, i, z, r):
        """
        evaluates the r-th order derivative of G_i the transition function of de Boor for the nodes xi_i, xi_{i+p+1}
        """
        assert 1 <= r <= self._p+1
        assert 0 <= i < self._n
        dz = z - self._Xi[i]
        dx = self._Xi[i+self._p+1] - self._Xi[i]

        if 0 <= dz < dx:
            return (2/dx)**r * self.der_perfect_spline((2*dz-dx)/dx, r-1)
        else:
            return 0

    def _psi_D(self, i, x):
        """
        duals to the _phi B-splines, of de Boor kind
        """
        p = self._p
        val = 0
        dx = [x-self._Xi[i+a+1] for a in range(p)]
        if self._Xi[i] <= x < self._Xi[i+p+1]:
            if p == 1:
                val = (
                    self.der_transition_function(i, x, 2)*dx[0]
                   + 2*self.der_transition_function(i, x, 1)
                )
            elif p == 2:
                val = 1./2 *(
                    self.der_transition_function(i, x, 3)*dx[0]*dx[1]
                    + 3*self.der_transition_function(i, x, 2)*(dx[0]+dx[1])
                    + 6*self.der_transition_function(i, x, 1)
                )
            elif p == 3:
                val = 1./6 *(
                    self.der_transition_function(i, x, 4)*dx[0]*dx[1]*dx[2]
                    + 4*self.der_transition_function(i, x, 3)*(dx[0]*dx[1] + dx[1]*dx[2] + dx[2]*dx[0])
                    + 12*self.der_transition_function(i, x, 2)*(dx[0]+dx[1]+dx[2])
                    + 24*self.der_transition_function(i, x, 1)
                )
            else:
                raise ValueError("der_perfect_spline is only implemented for p <= 3")

        return val

    def _psi_M_aux(self, i, x):
        """
        For i a dof index associated with a macro-element, these auxiliary functions form a basis of PP_m
        and they are duals to the splines phi_j (for j an index associated to the same macro-element)

        They are expressed in a Bernstein basis of the macro-element ell
        """
        assert i in range(self._n)
        p = self._p
        M = self._M_p
        m = self._m
        ell = self.macro_element_index_of_dof(i)
        val = 0
        if self._Xi[ell*M + p] <= x < self._Xi[(ell+1)*M + p]:
            a = self._local_index_of_macro_element_dof(i, ell)
            for b in range(m+1):
                val += self._psi_M_aux_coefs[ell][a,b] * self._bernstein_M(b,ell,x)
        return val

    def _psi_M(self, i, x):
        """
        dual function of macro-element kind
        """
        # here we assume that the M dual basis is of MP kind
        if self.dof_index_is_macro_vertex(i):
            val = self._psi_P(i,x)
        else:
            val = self._psi_M_aux(i,x)
            ell = self.macro_element_index_of_dof(i)
            a = self._local_index_of_macro_element_dof(i, ell)
            for b in range(self._p):
                # corrections with left macro-vertex duals
                j = self._global_index_of_macro_vertex_dof(ell, b)
                val -= self._left_correction_products_psi_M_aux[ell][a,b] * self._psi_M(j,x)
                # corrections with right macro-vertex duals
                j = self._global_index_of_macro_vertex_dof(ell+1, b)
                val -= self._right_correction_products_psi_M_aux[ell][a,b] * self._psi_M(j,x)
        return val


    def _psi(self, i, x, kind='P'):
        """
        duals to the _phi B-splines
        """
        assert i in range(self._n)
        val = 0
        if kind == 'P':
            # then this dual function has the same support as phi_i
            val = self._psi_P(i,x)
        elif kind == 'D':
            val = self._psi_D(i,x)
        elif kind == 'M':
            val = self._psi_M(i,x)
        else:
            raise ValueError("dual kind unknown: "+repr(kind))
        return val

    def get_mass_matrix(self):
        """
        return the standard mass matrix on the smooth spline space
        """
        p = self._p
        n_contributions = self._N_cells*(p+1)*(p+1)
        row = np.zeros((n_contributions), dtype = int)
        col = np.zeros((n_contributions), dtype = int)
        data = np.zeros((n_contributions), dtype = float)
        l = 0
        for k in range(self._N_cells):
            for i in range(k, k+p+1):
                for j in range(k, k+p+1):
                    row[l] = i
                    col[l] = j
                    data[l] = quadrature(
                        lambda x: self._phi(i, x)*self._phi(j, x),
                        self._Xi[k+p],
                        self._Xi[k+p+1],
                        maxiter=2*self._p+1,
                        vec_func=False,
                    )[0]
                    l += 1
        sparse_matrix = coo_matrix((data, (row, col)), shape=(self._n, self._n))
        return sparse_matrix

    def index_tilde_dof(self, s, i):
        assert 0 <= s < self._N_subdomains
        assert 0 <= i < self._sub_n
        return i + s * self._sub_n

    def get_tilde_mass_matrix(self):
        """
        return the standard mass matrix on the discontinuous spline space tilde_V
        """
        p = self._p
        n_contributions = self._N_subdomains * self._N_cells_sub * (p+1)*(p+1)
        row = np.zeros((n_contributions), dtype = int)
        col = np.zeros((n_contributions), dtype = int)
        data = np.zeros((n_contributions), dtype = float)
        l = 0
        for s in range(self._N_subdomains):
            for k in range(self._N_cells_sub):
                for i in range(k, k+p+1):
                    for j in range(k, k+p+1):
                        row[l] = self.index_tilde_dof(s,i)
                        col[l] = self.index_tilde_dof(s,j)
                        data[l] = quadrature(
                            lambda x: self._tilde_phi(x, s=s, i=i)*self._tilde_phi(x,s=s,i=j),
                            self._Xi[k+p],
                            self._Xi[k+p+1],
                            maxiter=2*self._p+1,
                            vec_func=False,
                        )[0]
                        l += 1
        sparse_matrix = coo_matrix((data, (row, col)), shape=(self._tilde_n, self._tilde_n))
        return sparse_matrix

    def smooth_proj_on_tilde_V(self, kind='P'):
        """
        return the operator matrix for the smoothing operator tilde V -> V
        entries are P_{i,g} = sigma_i(P tilde_phi_g)
        with  g = g(s,j) = j + s * self._sub_n  the global index of the basis function tilde_phi_{s,j}

        implementation details:
        P is defined by the dual basis functions psi_i (of specified kind), with
        sigma_i(P tilde_phi_g) = < psi_i, tilde_phi_g >
        to compute these products we use the fact that
         - the support of tilde_phi_g is in the subdomain s (by definition)
         - and the support of psi_i is in the union of at most two subdomains s_i, s_i+1
        thanks to the requirement that
            -) self._N_cells_sub >= p+1 (if no macro-element duals are used)
            -) self._N_cells_sub >= 2*p + M = 3*p+m+1  (otherwise)
        indeed, the support of psi_i consists:
            -) p+1 cells (at most) in the first case
            -) 2*p + M cells (at most) in the second case
        therefore supp(psi_i) cannot intersect more than two subdomains
        """
        row = []
        col = []
        data = []
        p = self._p
        if kind == 'P':
            max_quad_order = 2*p + 1  # dual functions are pw pol with local degree <= p
        elif kind == 'M':
            max_quad_order = p+max(p, self._m)+1  # dual functions are pw pol with local degree <= max(p, m)
        else:
            max_quad_order = 50  # default value (DeBoor duals are also of local degree <= p but pol pieces do not match)
        for i in range(self._n):
            i0 = self.i_first_knot_supp_psi(i, kind=kind)
            s0 = (i0 - p) // self._N_cells_sub  # subdomain s0 contains (xi_i0, xi_{i0+1})
            i1 = self.i_last_knot_supp_psi(i, kind=kind)
            s1 = (i1-1 - p) // self._N_cells_sub  # subdomain s1 contains (xi_{i1-1}, xi_i1)
            assert s0 <= s1 <= s0+1
            for s in range(s0, s1+1):
                # then loop on the cells of each subdomain, and on each local spline that intersect this cell
                for k in range(self._N_cells_sub):
                    for j in range(k, k+p+1):
                        g = self.index_tilde_dof(s,j)
                        row.append( i )
                        col.append( g )
                        data.append(
                            quadrature(
                                lambda x: self._psi(i, x)*self._tilde_phi(x, s=s, i=j),
                                self._tilde_Xi[k+p],
                                self._tilde_Xi[k+p+1],
                                maxiter=max_quad_order,
                                vec_func=False,
                            )[0]
                        )

        sparse_matrix = coo_matrix((data, (row, col)), shape=(self._tilde_n, self._tilde_n))
        return sparse_matrix

    def get_moments(self, f):
        """
        return the moments of f against spline basis functions (of V_h)
        """
        p = self._p
        moments = np.zeros(self._n)
        for k in range(self._N_cells):
            for i in range(k, k+p+1):
                moments[i] += quad(
                    lambda x: self._phi(i, x)*f(x),
                    self._Xi[k+p],
                    self._Xi[k+p+1],
                    # maxiter=2*self._p,
                    # vec_func=False,
                )[0]
        return moments

    def get_tilde_moments(self, f):
        """
        return the moments of f against the discontinuous spline basis functions of tilde_V_h
        """
        p = self._p
        moments = np.zeros(self._tilde_n)
        for s in range(self._N_subdomains):
            for k in range(self._N_cells_sub):
                for i in range(k, k+p+1):
                    g = self.index_tilde_dof(s,i)
                    moments[g] += quad(
                        lambda x: self._tilde_phi(x, s=s, i=i)*f(x),
                        self._tilde_Xi[k+p],
                        self._tilde__Xi[k+p+1],
                    )[0]
        return moments

    def smooth_proj(self, f, kind='P', localize_quadratures=True, check=False):
        if kind=='L2':
            self.l2_proj(f)
        else:
            print(" -- PROJ -- kind=", kind)
            self.local_smooth_proj(
                f,
                kind=kind,
                localize_quadratures=localize_quadratures,
                check=check
            )

    def i_first_knot_supp_psi(self, i, kind=None):
        """
        return the index i0 of the first knot attached to the dual function psi_i of specified kind
        in particular i0 is such that  x < xi_i0  =>  psi_i(x) = 0
        """
        assert kind is not None
        if kind in ['P','D'] or self.dof_index_is_macro_vertex(i):
            return i
        else:
            ell = self.macro_element_index_of_dof(i)
            # support of the dual functions is (contained in) the union of those of
            # the dual functions associated with the left and right macro-vertices
            return ell*self._M_p

    def i_last_knot_supp_psi(self, i, kind=None):
        """
        return the index i1 of the last knot attached to the dual function psi_i of specified kind
        in particular i1 is such that  xi_i1 < x  =>  psi_i(x) = 0
        """
        assert kind is not None
        if kind in ['P','D'] or self.dof_index_is_macro_vertex(i):
            return i+self._p+1
        else:
            ell = self.macro_element_index_of_dof(i)
            # support of the dual functions is (contained in) the union of those of
            # the dual functions associated with the left and right macro-vertices
            return (ell+1)*self._M_p + 2*self._p

    def local_smooth_proj(self, f, kind='P', localize_quadratures=True, check=False):
        """
        project on smooth spline space using local dual functionals
        """
        grid = construct_grid_from_knots(self._p, self._n, self._Xi)
        for i in range(self._n):
            if localize_quadratures:
                x_min = self._Xi[self.i_first_knot_supp_psi(i, kind=kind)]
                x_max = self._Xi[self.i_last_knot_supp_psi(i, kind=kind)]
                # if kind in ['P','D'] or self.dof_index_is_macro_vertex(i):
                #     x_min = self._Xi[i]
                #     x_max = self._Xi[i+self._p+1]
                # else:
                #     ell = self.macro_element_index_of_dof(i)
                #     # support of the dual functions is (contained in) the union of those of
                #     # the dual functions associated with the left and right macro-vertices
                #     x_min = self._Xi[ell*self._M_p]
                #     x_max = self._Xi[(ell+1)*self._M_p + 2*self._p]
            else:
                x_min = self._x_min
                x_max = self._x_max
            if kind in ['D']:
                x_min_i = self._Xi[i]
                x_max_i = self._Xi[i+self._p+1]
                sing_points = list(map(lambda x: x_min_i + (x+1)/2*(x_max_i-x_min_i), self._sing_points_perfect_spline()))
                # the case of MD duals is more involved
            else:
                sing_points = self._Xi

            valid_points = []
            for s in sing_points:
                if x_min < s < x_max:
                    valid_points.append(s)
                    #list(map(lambda x: min(x_max, max(x_min, x)), sing_points))
            if len(valid_points) == 0:
                valid_points = None

            # print("valid_points = ", valid_points)
            self.coefs[i] = quad(
                lambda x: f(x)*self._psi(i, x, kind=kind), x_min, x_max,
                points=valid_points, limit=100,
                # vec_func=False,
            )[0]

        if check:
            print("check -- "+repr(kind)+" duals:  <tilde phi_i, phi_j>")
            coef_check = np.zeros((self._n,self._n))
            for i in range(self._n):
                for j in range(self._n):
                    coef_check[i,j] = _my_L2_prod(
                        lambda x:self._psi(i,x, kind=kind),
                        lambda x:self._phi(j, x),
                        sing_points=grid,
                    )
            print(coef_check)

    def l2_proj_V(self, f):
        """
        L2 projection on the smooth V space
        """
        print("L2 proj -- get mass matrix")
        mass = self.get_mass_matrix()
        print("L2 proj -- get f moments")
        f_moments = self.get_moments(f)
        self.coefs[:] = solve(mass, f_moments)

    def l2_proj_tilde_V(self, f):
        """
        L2 projection on the discontinuous tilde_V space
        """
        print("L2 proj on tilde V -- get mass matrix")
        mass = self.get_tilde_mass_matrix()
        print("L2 proj on tilde V -- get f moments")
        f_moments = self.get_tilde_moments(f)
        self.tilde_coefs[:] = solve(mass, f_moments)

    def histopolation_on_sub_domain(self, f, sub='left'):
        """
        histopolation (??) derived from test_projector_1d by ARA
        """
        if sub == 'left':
            n = self._n_left
            T = self._T_left
        else:
            assert sub == 'right'
            n = self._n_right
            T = self._T_right
        p = self._p
        print("Histopolation on subdomain "+sub)
        print("n = "+repr(n))
        print("T = "+repr(T))
        I0, I1 = interpolation_matrices(p, n, T)
        histopolation = Integral(p, n, T, kind='greville')
        f_1 = solve(I1, histopolation(f))
        # scale fh_1 coefficients
        S = scaling_matrix(p, n, T, kind='L2')
        f_1 = S.dot(f_1)
        tck = get_tck('L2', p, n, T, f_1)
        assert len(tck[1]) == n    # assert FAILS ?
        if sub == 'left':
            self.coefs_left = tck[1]
        else:
            assert sub == 'right'
            self.coefs_right = tck[1]

    def eval_discontinuous_spline(self, x):
        if x <= self._x_sep:
            tck = [self._T_left, self.coefs_left, self._p]
        else:
            tck = [self._T_right, self.coefs_right, self._p]
        return splev(x, tck)

    def eval_continuous_spline_splev(self, x):
        tck = [self._Xi, self.coefs, self._p]
        return splev(x, tck)

    def eval_continuous_spline(self, x):
        val = 0
        for i in range(self._n):
            val += self.coefs[i] * self._phi(i, x)
        return val

    def plot_spline(
            self,
            filename,
            spline_kind='continuous',
            N_points=100,
            ltype='-',
            f_ref=None,
            legend=None,
            title=None,
            legend_loc='lower left',
            iqnorm=0.5,
            save_plot=True
    ):
        """
        plot a spline
        Plot also some f_ref if given, and return the evaluated difference in some Lq
        :param iqnorm: 1/q to measure ||spline - f_ref||_{Lq}
        """
        vis_grid, vis_h = np.linspace(self._x_min, self._x_max, N_points, endpoint=False, retstep=True)
        if spline_kind == 'continuous':
            vals = [self.eval_continuous_spline_splev(xi) for xi in vis_grid]
        else:
            assert spline_kind == 'discontinuous'
            vals = [self.eval_discontinuous_spline(xi) for xi in vis_grid]
        if save_plot:
            fig = plt.figure()
            plt.clf()
            image = plt.plot(vis_grid, vals, ltype, color='b', label=spline_kind)
            if title is not None:
                plt.title(title)
        if f_ref is not None:
            vals_ref = [f_ref(xi) for xi in vis_grid]
            if iqnorm == 0:
                diff_norm = max(map(abs,np.subtract(vals,vals_ref)))
            else:
                diff_norm = (vis_h*sum(map(lambda x:abs(x)**(1./iqnorm), np.subtract(vals,vals_ref)[:N_points])))**iqnorm
            if save_plot:
                image = plt.plot(vis_grid, vals_ref, ltype, color='r', label="f ref")
        else:
            diff_norm = None
        if save_plot:
            plt.legend(loc=legend_loc)
            fig.savefig(filename)
            plt.clf()
        return diff_norm

def _my_L2_prod(f, g, xmin=0, xmax=1, sing_points=None, eps=None):
    """
    L2 product of f and g, using a scipy quadrature

    sing_points:
        sequence of break points in the bounded integration interval
        where local difficulties of the integrand may occur (e.g., singularities, discontinuities)
    eps:
        tolerance
    """
    if sing_points is not None:
        valid_points = list(map(lambda x: min(xmax, max(xmin, x)), sing_points))
    else:
        valid_points = None

    if eps is not None:
        epsabs = eps
        epsrel = eps
    else:
        epsabs = 1e-10
        epsrel = 1e-10
    #print("fg(0.6) = ", f(0.6)*g(0.6) )
    #print("valid_points = ", valid_points)
    return quad(lambda x: f(x)*g(x), xmin, xmax, points=valid_points, epsabs=epsabs, epsrel=epsrel, limit=100)[0]

def trunc_pow(z, q):
    """
    computes the truncated power (z)^q_+, also called Macaulay bracket
    """
    if z < 0:
        return 0
    elif int(q) == 0:
        return 1
    else:
        return z**q
