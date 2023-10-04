"""
Script with functions for quadratures, moment basis for 1D-3D spatial dimensions
Author:  Steffen Schotthöfer
Date: 16.03.21
"""

import numpy as np
import scipy
import scipy.optimize as opt
import tensorflow as tf
from numpy.polynomial.legendre import leggauss


class EntropyTools:
    """
    Same functions implemented in the sobolev Network.
    Also uses Tensorflow
    """
    spatial_dimension: int
    poly_degree: int
    nq: int
    input_dim: int
    quadPts: tf.Tensor  # dims = (1 x nq)
    quadWeights: tf.Tensor  # dims = (1 x nq)
    moment_basis_tf: tf.Tensor  # dims = (batchSIze x N x nq)
    opti_u: np.ndarray
    moment_basis_np: np.ndarray

    # @brief: Regularization Parameter for regularized entropy. =0 means non regularized
    regularization_gamma: tf.Tensor
    regularization_gamma_np: float
    # @brief: tensor of the form [0,gamma,gamma,...]
    regularization_gamma_vector: tf.Tensor

    def __init__(self, polynomial_degree=1, spatial_dimension=1, gamma=0, basis="monomial") -> object:
        """
        Class to compute the 1D entropy closure up to degree N
        input: N  = degree of polynomial basis
        """

        # Create quadrature and momentBasis. Currently only for 1D problems
        self.poly_degree = polynomial_degree
        self.spatial_dimension = spatial_dimension
        quad_order = 100
        if spatial_dimension == 1 and basis == "monomial":
            self.nq = quad_order
            [quad_pts, quad_weights] = qGaussLegendre1D(quad_order)  # order = nq
            m_basis = computeMonomialBasis1D(quad_pts, self.poly_degree)  # dims = (N x nq)
        if spatial_dimension == 2 and basis == "monomial":
            [quad_pts, quad_weights, _, _] = qGaussLegendre2D(quad_order)  # dims = nq
            self.nq = quad_weights.size  # is not 10 * polyDegree
            m_basis = computeMonomialBasis2D(quad_pts, self.poly_degree)  # dims = (N x nq)
        elif spatial_dimension == 3 and basis == "spherical_harmonics":
            [quad_pts, quad_weights, mu, phi] = qGaussLegendre3D(6 * polynomial_degree)  # dims = nq
            self.nq = quad_weights.size  # is not 20 * polyDegree
            m_basis = compute_spherical_harmonics(mu, phi, self.poly_degree)
        elif spatial_dimension == 2 and basis == "spherical_harmonics":
            [quad_pts, quad_weights, mu, phi] = qGaussLegendre2D(6 * polynomial_degree)  # dims = nq #
            self.nq = quad_weights.size  # is not 20 * polyDegree
            # print(sum(quad_weights))
            m_basis = compute_spherical_harmonics_2D(mu, phi, self.poly_degree)
            # np.set_printoptions(precision=2)
            # print(quad_weights)  # weights ok
            # print(np.sum(quad_weights))  # sumweights ok
            # print("----")
            # print(mu)
            # print(phi)
            # print(m_basis)
            # print(m_basis.transpose())  # basis ok
        else:
            print("spatial dimension not yet supported for sobolev wrapper")
            exit()
        self.quadPts = tf.constant(quad_pts, shape=(self.spatial_dimension, self.nq), dtype=tf.float64)
        self.quadWeights = tf.constant(quad_weights, shape=(1, self.nq), dtype=tf.float64)

        self.quad_weights_np = quad_weights

        self.input_dim = m_basis.shape[0]
        self.moment_basis_tf = tf.constant(m_basis, shape=(self.input_dim, self.nq), dtype=tf.float64)
        self.moment_basis_np = m_basis
        # self.moment_basis_orig = tf.constant(m_basis, shape=(self.input_dim, self.nq), dtype=tf.float64)

        self.regularization_gamma_np = gamma
        self.regularization_gamma = tf.constant(gamma, dtype=tf.float64)
        gamma_vec = gamma * np.ones(shape=(1, self.input_dim))
        gamma_vec[0, 0] = 0.0  # partial regularization
        self.regularization_gamma_vector = tf.constant(gamma_vec, dtype=tf.float64, shape=(1, self.input_dim))

    def rotate_basis(self, rot_mat):
        self.moment_basis_np = rot_mat @ self.moment_basis_np

    # def reset_basis(self):
    #    self.moment_basis_tf = np.copy(self.moment_basis_orig)

    def reconstruct_alpha(self, alpha: np.ndarray) -> np.ndarray:
        f_quad = np.exp(np.einsum("i,iq->q", alpha, self.moment_basis_np[1:, :]))
        alpha_0 = - (np.log(self.moment_basis_np[0, 0]) + np.log(np.einsum('q,q->', f_quad, self.quad_weights_np)
                                                                 )) / self.moment_basis_np[0, 0]
        return np.append(alpha_0, alpha)

    def reconstruct_u(self, alpha: np.ndarray) -> np.ndarray:

        f_quad = np.exp(np.einsum("i,iq->q", alpha, self.moment_basis_np))
        recons_u = np.einsum('q,q,iq->i', f_quad, self.quad_weights_np, self.moment_basis_np)  # f*w*m
        t3 = self.regularization_gamma_np * alpha
        t3[0] = 0.0
        return recons_u + t3

    def compute_u(self, f: tf.Tensor) -> tf.Tensor:
        return np.einsum('q,q,iq->i', f, self.quad_weights_np, self.moment_basis_np)  # <fm>

    def compute_h_dual(self, u: np.ndarray, alpha: np.ndarray) -> np.ndarray:
        f_quad = np.exp(np.einsum("i,iq->q", alpha, self.moment_basis_np))
        t1 = np.einsum('q,q->', f_quad, self.quad_weights_np)  # <f>
        t2 = np.einsum("i,i->", alpha, u)
        t3 = self.regularization_gamma_np / 2.0 * np.linalg.norm(alpha[1:]) ** 2
        return -1 * (t1 - t2 + t3)

    def compute_h_primal(self, f: np.ndarray) -> float:
        """
        brief: computes the entropy functional h on u and alpha

        returns h = <f*ln(f)-f>
        """
        # Currently only for maxwell Boltzmann entropy
        eta_f = f * np.log(f) - f
        return np.einsum("q,q->", eta_f, self.quad_weights_np)

    def integrate_f(self, f: np.ndarray):

        return np.einsum("q,q->", f, self.quad_weights_np)

    def minimize_entropy(self, u: np.ndarray, alpha_start: np.ndarray) -> np.ndarray:
        """
        brief: computes the minimal entropy at u
        input: u = dims (1,N)
           start =  start_valu of alpha
        """
        self.opti_u = np.copy(u)
        opt_result = opt.minimize(fun=self.opti_entropy, x0=alpha_start, jac=self.opti_entropy_prime,
                                  hess=self.opti_entropy_prime2, tol=1e-6)

        if not opt_result.success:
            print("Moment")
            print(u)
            print("Optimization unsuccessfull!")

        return opt_result.x

    def opti_entropy(self, alpha: np.ndarray) -> np.ndarray:

        f_quad = np.exp(np.einsum("i,iq->q", alpha, self.moment_basis_np))
        t1 = np.einsum('q,q->', f_quad, self.quad_weights_np)  # f*w
        t2 = np.einsum("i,i->", alpha, self.opti_u)
        t3 = self.regularization_gamma_np / 2.0 * np.linalg.norm(alpha[1:]) ** 2
        return t1 - t2 + t3

    def opti_entropy_prime(self, alpha: np.ndarray) -> np.ndarray:

        f_quad = np.exp(np.einsum("i,iq->q", alpha, self.moment_basis_np))
        t1 = np.einsum('q,q,iq->i', f_quad, self.quad_weights_np, self.moment_basis_np)  # f*w*m
        t3 = self.regularization_gamma_np * alpha
        t3[0] = 0.0
        return t1 - self.opti_u + t3

    def opti_entropy_prime2(self, alpha: np.ndarray) -> np.ndarray:
        """
         brief: returns the 2nd derivative negative entropy functional with fixed u
         nS = batchSize
         N = basisSize
         nq = number of quadPts

         input: alpha, dims = (1 x N)
                u, dims = (1 x N)
         used members: m    , dims = (N x nq)
                     w    , dims = nq

         returns h =  <mxm*eta_*(alpha*m)>
        """
        # Currently only for maxwell Boltzmann entropy
        f_quad = np.exp(np.einsum("i,iq->q", alpha, self.moment_basis_np))

        hess = np.einsum("q,q,iq,jq-> ij", f_quad, self.quad_weights_np, self.moment_basis_np, self.moment_basis_np)

        t3 = self.regularization_gamma_np * np.identity((self.input_dim, self.input_dim))
        t3[0, 0] = 0
        return -hess + t3


# Standalone features


# Integration


def qGaussLegendre1D(order: int):
    """
    order: order of quadrature
    returns: [mu, weights] : quadrature points and weights
    """
    return leggauss(order)


def qGaussLegendre2D(Qorder):
    """
       order: order of quadrature, uses all quadpts... inefficient
       returns: [pts, weights] : quadrature points and weights, dim(pts) = nq x 2
    """

    def computequadpoints(order):
        """Quadrature points for GaussLegendre quadrature. Read from file."""
        """
        mu in  [-1,0]
        phi in [0,2*pi]
        """
        mu, _ = leggauss(order)
        phi = [np.pi * (k + 1 / 2) / order for k in range(2 * order)]
        xy = np.zeros((order * order, 2))
        count = 0
        mu_arr = np.zeros((order * order,))
        phi_arr = np.zeros((order * order,))
        for i in range(int(order / 2.0)):
            for j in range(2 * order):
                mu_arr[count] = mu[i]
                phi_arr[count] = phi[j]
                mui = mu[i]
                phij = phi[j]
                xy[count, 0] = np.sqrt(1 - mui ** 2) * np.cos(phij)
                xy[count, 1] = np.sqrt(1 - mui ** 2) * np.sin(phij)
                # xyz[count, 2] = mui
                count += 1

        return xy, mu_arr, phi_arr

    def computequadweights(order):
        """Quadrature weights for GaussLegendre quadrature. Read from file."""
        _, leggaussweights = leggauss(order)
        w = np.zeros(order * order)
        count = 0
        for i in range(int(order / 2.0)):
            for j in range(2 * order):
                w[count] = 2.0 * np.pi / order * leggaussweights[i]
                count += 1
        return w

    pts, mu, phi = computequadpoints(Qorder)
    weights = computequadweights(Qorder)

    return [pts, weights, mu, phi]


def qGaussLegendre3D(Qorder):
    """
       order: order of quadrature, uses all quadpts... inefficient
       returns: [pts, weights] : quadrature points and weights, dim(pts) = nq x 2
    """

    def computequadpoints(order):
        """Quadrature points for GaussLegendre quadrature. Read from file."""
        mu, _ = leggauss(order)
        phi = [np.pi * (k + 1 / 2) / order for k in range(2 * order)]
        xyz = np.zeros((2 * order * order, 3))
        count = 0
        mu_arr = np.zeros((2 * order * order,))
        phi_arr = np.zeros((2 * order * order,))

        for i in range(int(order)):
            for j in range(2 * order):
                mu_arr[count] = mu[i]
                phi_arr[count] = phi[j]

                xyz[count, 0] = np.sqrt(1 - mu[i] ** 2) * np.cos(phi[j])
                xyz[count, 1] = np.sqrt(1 - mu[i] ** 2) * np.sin(phi[j])
                xyz[count, 2] = mu[i]
                count += 1

        return xyz, mu_arr, phi_arr

    def computequadweights(order):
        """Quadrature weights for GaussLegendre quadrature. Read from file."""
        _, leggaussweights = leggauss(order)
        w = np.zeros(2 * order * order)
        count = 0
        for i in range(int(order)):
            for j in range(2 * order):
                w[count] = np.pi / order * leggaussweights[i]
                count += 1
        return w

    pts, mu, phi = computequadpoints(Qorder)
    weights = computequadweights(Qorder)

    return [pts, weights, mu, phi]


def integrate(integrand, weights):
    """
    params: weights = quadweights vector (at quadpoints) (dim = nq)
            integrand = integrand vector, evaluated at quadpts (dim = vectorlen x nq)
    returns: integral <integrand>
    """
    return np.dot(integrand, weights)


# Entropy functions

def negEntropyFunctional(u, alpha, m, w):
    """
    compute entropy functional at one point using
    inputs: u = moment vector, dim = N+1
            alpha = corresponding lagrange multiplier, dim = N+1
            m = moment basis vector, evaluated at quadpts, dim = (N + 1) x nQuad
            quadPts = number of quadpts
    returns: h = alpha*u - <entropyDual(alpha*m)>
    """
    # tmp = integrate(entropyDualPrime(np.matmul(alpha, m)), w)
    return 0  # Todo


def entropy(x):
    return x * np.log(x) - x


def entropyDual(y):
    return np.exp(y)


def entropyPrime(x):
    return np.log(x)


def entropyDualPrime(y):
    return np.exp(y)


def reconstructU(alpha, m, w):
    """
    imput: alpha, dims = (nS x N)
           m    , dims = (N x nq)
           w    , dims = nq
    returns u = <m*eta_*'(alpha*m)>, dim = (nS x N)
    """

    # tensor version
    temp = entropyDualPrime(np.matmul(alpha, m))  # ns x nq
    # extend to 3D tensor
    mTensor = m.reshape(1, m.shape[0], m.shape[1])  # ns x N  x nq
    tempTensor = temp.reshape(temp.shape[0], 1, temp.shape[1])  # ns x N x nq

    return integrate(mTensor * tempTensor, w)


def reconstructL1F(alpha, m, w):
    """
    imput: alpha, dims = (nS x N)
           m    , dims = (N x nq)
           w    , dims = nq
    returns:  the L1 norm of f, the kinetic density, <|f|>
    """
    return integrate(np.abs(entropyDualPrime(np.matmul(alpha, m))), w)


def reconstructUSingleCell(alpha, m, w):
    """
    imput: alpha, dims = (N)
           m    , dims = (N x nq)
           w    , dims = nq
    returns u = <m*eta_*'(alpha*m)>, dim = (nS x N)
    """

    temp = entropyDualPrime(np.matmul(alpha, m))  # ns x nq
    res = m * temp

    return integrate(res, w)


# Basis Computation
def computeMonomialBasis1D(quadPts, polyDegree):
    """
    params: quadPts = quadrature points to evaluate
            polyDegree = maximum degree of the basis
    return: monomial basis evaluated at quadrature points
    """
    basisLen = getBasisSize(polyDegree, 1)
    nq = quadPts.shape[0]
    monomialBasis = np.zeros((basisLen, nq))

    for idx_quad in range(0, nq):
        for idx_degree in range(0, polyDegree + 1):
            monomialBasis[idx_degree, idx_quad] = np.power(quadPts[idx_quad], idx_degree)
    return monomialBasis


def computeMonomialBasis2D(quadPts, polyDegree):
    """
    brief: Same basis function ordering as in KiT-RT code
    params: quadPts = quadrature points to evaluate
            polyDegree = maximum degree of the basis
    return: monomial basis evaluated at quadrature points
    """
    basisLen = getBasisSize(polyDegree, 2)
    nq = quadPts.shape[0]
    monomialBasis = np.zeros((basisLen, nq))

    for idx_quad in range(0, nq):
        # Hardcoded for degree 1
        # monomialBasis[0, idx_quad] = 1.0
        # monomialBasis[1, idx_quad] = quadPts[idx_quad, 0]
        # monomialBasis[2, idx_quad] = quadPts[idx_quad, 1]

        omega_x = quadPts[idx_quad, 0]
        omega_y = quadPts[idx_quad, 1]

        idx_vector = 0
        for idx_degree in range(0, polyDegree + 1):
            for a in range(0, idx_degree + 1):
                b = idx_degree - a
                monomialBasis[idx_vector, idx_quad] = np.power(omega_x, a) * np.power(omega_y, b)
                idx_vector += 1

    return monomialBasis


def getBasisSize(polyDegree, spatialDim):
    """
    params: polyDegree = maximum Degree of the basis
            spatialDIm = spatial dimension of the basis
    returns: basis size
    """

    basisLen = 0

    for idx_degree in range(0, polyDegree + 1):
        basisLen += int(
            getCurrDegreeSize(idx_degree, spatialDim))

    return basisLen


def getCurrDegreeSize(currDegree, spatialDim):
    """
    Computes the number of polynomials of the current spatial dimension
    """
    return np.math.factorial(currDegree + spatialDim - 1) / (
            np.math.factorial(currDegree) * np.math.factorial(spatialDim - 1))


# --- spherical harmonics
def compute_spherical_harmonics(mu: np.ndarray, phi: np.ndarray, degree: int) -> np.ndarray:
    # assemble spherical harmonics
    n_system = 2 * degree + degree ** 2 + 1
    sh_basis = np.zeros((n_system, len(mu)))

    for i in range(len(mu)):
        sh_basis[0, i] = np.sqrt(1 / (4 * np.pi))
        if degree > 0:
            sh_basis[1, i] = -np.sqrt(3. / (4 * np.pi)) * np.sqrt(1 - mu[i] * mu[i]) * np.sin(phi[i])
            sh_basis[2, i] = np.sqrt(3. / (4 * np.pi)) * mu[i]
            sh_basis[3, i] = -np.sqrt(3. / (4 * np.pi)) * np.sqrt(1 - mu[i] * mu[i]) * np.cos(phi[i])
        if degree > 1:
            sh_basis[4, i] = np.sqrt(15. / (16. * np.pi)) * (1 - mu[i] * mu[i]) * np.sin(2 * phi[i])
            sh_basis[5, i] = -1 * np.sqrt(15. / (4. * np.pi)) * mu[i] * np.sqrt(1 - mu[i] * mu[i]) * np.sin(phi[i])
            sh_basis[6, i] = np.sqrt(5. / (16. * np.pi)) * (3 * mu[i] * mu[i] - 1)
            sh_basis[7, i] = -1 * np.sqrt(15. / (4. * np.pi)) * mu[i] * np.sqrt(1 - mu[i] * mu[i]) * np.cos(phi[i])
            sh_basis[8, i] = np.sqrt(15. / (16. * np.pi)) * (1 - mu[i] * mu[i]) * np.cos(2 * phi[i])

    return sh_basis


def compute_spherical_harmonics_2D(mu: np.ndarray, phi: np.ndarray, degree: int) -> np.ndarray:
    # Tested against KiT-RT for degree 0-4 at 6th June 2023
    # assemble spherical harmonics
    input_dim_dict_2D: dict = {1: 3, 2: 6, 3: 10, 4: 15, 5: 21}

    n_system = input_dim_dict_2D[degree]
    sh_basis_scipy = np.zeros((n_system, len(mu)))

    # sh_basis = np.zeros((n_system, len(mu)))
    # for i in range(len(mu)):
    #    sh_basis[0, i] = np.sqrt(1 / (4 * np.pi))
    #    if degree > 0:
    #        sh_basis[1, i] = - np.sqrt(3. / (4 * np.pi)) * np.sqrt(1 - mu[i] * mu[i]) * np.sin(phi[i])
    #        sh_basis[2, i] = - np.sqrt(3. / (4 * np.pi)) * np.sqrt(1 - mu[i] * mu[i]) * np.cos(phi[i])
    #    if degree > 1:
    #        sh_basis[3, i] = np.sqrt(15. / (16. * np.pi)) * (1 - mu[i] * mu[i]) * np.sin(2 * phi[i])
    #        sh_basis[4, i] = np.sqrt(5. / (16. * np.pi)) * (3 * mu[i] * mu[i] - 1)
    #        sh_basis[5, i] = np.sqrt(15. / (16. * np.pi)) * (1 - mu[i] * mu[i]) * np.cos(2 * phi[i])

    for i in range(len(mu)):
        count = 0
        for l in range(0, degree + 1):
            for k in range(-l, l + 1):

                if (k + l) % 2 == 0:
                    if k < 0:
                        Y = scipy.special.sph_harm(np.abs(k), l, phi[i], np.arccos(mu[i]), out=None)
                        Y = np.sqrt(2) * (-1) ** (k + l) * Y.imag
                    if k > 0:
                        Y = scipy.special.sph_harm(k, l, phi[i], np.arccos(mu[i]), out=None)
                        Y = np.sqrt(2) * (-1) ** (k + l) * Y.real
                    if k == 0:
                        Y = scipy.special.sph_harm(k, l, phi[i], np.arccos(mu[i]), out=None)
                        Y = Y.real

                    sh_basis_scipy[count, i] = Y
                    count += 1

        # sh_basis_scipy[0, i] = np.sqrt(1 / (2 * np.pi))

        # test against python implementation
    return sh_basis_scipy


def compute_spherical_harmonics_general(mu: np.ndarray, phi: np.ndarray, degree: int) -> np.ndarray:
    # assemble spherical harmonics
    n_system = 2 * degree + degree ** 2 + 1
    sh_basis = np.zeros((n_system, len(mu)))
    idx_sys = 0
    for l in range(degree + 1):
        for k in range(-l, l + 1):
            idx_quad = 0
            for mui, phij in zip(mu, phi):
                Yvals = scipy.special.sph_harm(abs(k), l, phij, np.arccos(mui))
                if k < 0:
                    Yvals = np.sqrt(2) * Yvals.imag  # * (-1) ** (k + 1)
                elif k > 0:
                    Yvals = np.sqrt(2) * Yvals.real  # * (-1) ** (k + 1)
                elif k == 0:
                    Yvals = Yvals.real
                sh_basis[idx_sys, idx_quad] = Yvals
                idx_quad += 1
            idx_sys += 1

    return sh_basis


def create_sh_rotator_1D(u_1_in) -> [np.ndarray, np.ndarray]:
    theta = np.arctan2(u_1_in[0], u_1_in[1]) - np.pi / 2.0;
    c = np.cos(theta)
    s = np.sin(theta)

    G = np.zeros((3, 3))
    G[0, 0] = 1
    G[1, 1] = c
    G[2, 2] = c
    G[1, 2] = -s
    G[2, 1] = s
    return G[1:, 1:], G


def create_sh_rotator_2D(u_1_in) -> [np.ndarray, np.ndarray]:
    theta = np.arctan2(u_1_in[1], u_1_in[0])  # - np.pi / 2.0
    c = np.cos(theta)
    s = np.sin(theta)
    c2 = np.cos(2 * theta)
    s2 = np.sin(2 * theta)

    G = np.zeros((6, 6))
    G[0, 0] = 1
    G[1, 1] = c
    G[2, 2] = c
    G[1, 2] = s
    G[2, 1] = -s

    G[3, 3] = c2
    G[4, 4] = 1.0
    G[5, 5] = c2
    G[3, 5] = s2
    G[5, 3] = -s2
    # print(G)
    return G[1:, 1:], G
