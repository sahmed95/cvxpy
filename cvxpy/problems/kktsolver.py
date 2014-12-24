"""
Copyright 2013 Steven Diamond

This file is part of CVXPY.

CVXPY is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

CVXPY is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with CVXPY.  If not, see <http://www.gnu.org/licenses/>.
"""

# A custom KKT solver for CVXOPT that can handle redundant constraints.
# Uses regularization and iterative refinement.

from cvxopt import blas, lapack
from cvxopt.base import matrix
from cvxopt.misc import scale, pack, pack2, unpack, symm

# Regularization constant (denoted elsewhere by E).
REG_EPS = 1e-9

# Returns a kktsolver for linear cone programs (or nonlinear if F is given).
def get_kktsolver(G, dims, A, F=None):
    if F is None:
        factor = kkt_chol(G, dims, A)
        def kktsolver(W):
            return factor(W)
    else:
        mnl, x0 = F()
        factor = kkt_chol(G, dims, A, mnl)
        def kktsolver(x, z, W):
            f, Df, H = F(x, z)
            return factor(W, H, Df)
    return kktsolver

def kkt_chol(G, dims, A, mnl = 0):
    """
    Solution of KKT equations by reduction to a 2 x 2 system, a QR
    factorization to eliminate the equality constraints, and a dense
    Cholesky factorization of order n-p.

    Computes the QR factorization

        A' = [Q1, Q2] * [R; 0]

    and returns a function that (1) computes the Cholesky factorization

        Q_2^T * (H + GG^T * W^{-1} * W^{-T} * GG) * Q2 = L * L^T,

    given H, Df, W, where GG = [Df; G], and (2) returns a function for
    solving

        [ H    A'   GG'    ]   [ ux ]   [ bx ]
        [ A    0    0      ] * [ uy ] = [ by ].
        [ GG   0    -W'*W  ]   [ uz ]   [ bz ]

    H is n x n,  A is p x n, Df is mnl x n, G is N x n where
    N = dims['l'] + sum(dims['q']) + sum( k**2 for k in dims['s'] ).
    """

    p, n = A.size
    cdim = mnl + dims['l'] + sum(dims['q']) + sum([ k**2 for k in
        dims['s'] ])
    cdim_pckd = mnl + dims['l'] + sum(dims['q']) + sum([ int(k*(k+1)/2)
        for k in dims['s'] ])

    # A' = [Q1, Q2] * [R; 0]  (Q1 is n x p, Q2 is n x n-p).
    if type(A) is matrix:
        QA = A.T
    else:
        QA = matrix(A.T)
    tauA = matrix(0.0, (p,1))
    lapack.geqrf(QA, tauA)

    Gs = matrix(0.0, (cdim, n))
    K = matrix(0.0, (n,n))
    bzp = matrix(0.0, (cdim_pckd, 1))
    yy = matrix(0.0, (p,1))

    def factor(W, H = None, Df = None):

        # Compute
        #
        #     K = [Q1, Q2]' * (H + E*I + GG' * W^{-1} * W^{-T} * GG / (1 + E) + A'A/E) * [Q1, Q2]
        #
        # and take the Cholesky factorization of the 2,2 block
        #
        #     Q_2' * (H + GG^T * W^{-1} * W^{-T} * GG) * Q2.

        # Gs = W^{-T} * GG in packed storage.
        print "1"
        if mnl:
            Gs[:mnl, :] = Df
        Gs[mnl:, :] = G
        scale(Gs, W, trans = 'T', inverse = 'I')
        pack2(Gs, dims, mnl)

        # K = [Q1, Q2]' * (H + Gs' * Gs) * [Q1, Q2].
        blas.syrk(Gs, K, k = cdim_pckd, trans = 'T')
        if H is not None: K[:,:] += H
        print "2"
        symm(K, n)
        print "3"
        lapack.ormqr(QA, tauA, K, side = 'L', trans = 'T')
        print "4"
        lapack.ormqr(QA, tauA, K, side = 'R')
        print "5"

        # Cholesky factorization of 2,2 block of K.
        lapack.potrf(K, n = n-p, offsetA = p*(n+1))

        def solve(x, y, z):
            print "solving"
            # Solve
            #
            #     [ H+EPS      A'      GG'*W^{-1} ]   [ ux   ]   [ bx        ]
            #     [ A          -EPS    0          ] * [ uy   ] = [ by        ]
            #     [ W^{-T}*GG  0   -I-EPS         ]   [ W*uz ]   [ W^{-T}*bz ]
            #
            # and return ux, uy, W*uz.
            #
            # On entry, x, y, z contain bx, by, bz.  On exit, they contain
            # the solution ux, uy, W*uz.
            #
            # If we change variables ux = Q1*v + Q2*w, the system becomes
            #
            #     [ K11 K12    R ]   [ v  ]   [Q1'*(bx+GG'*W^{-1}*W^{-T}*bz)]
            #     [ K21 K22    0 ] * [ w  ] = [Q2'*(bx+GG'*W^{-1}*W^{-T}*bz)]
            #     [ R^T 0   -EPS ]   [ uy ]   [by                           ]
            #
            #     W*uz = W^{-T} * ( GG*ux - bz ).

            # bzp := W^{-T} * bz in packed storage
            scale(z, W, trans = 'T', inverse = 'I')
            pack(z, bzp, dims, mnl)
            print "6"
            # x := [Q1, Q2]' * (x + Gs' * bzp)
            #    = [Q1, Q2]' * (bx + Gs' * W^{-T} * bz)
            blas.gemv(Gs, bzp, x, beta = 1.0, trans = 'T', m = cdim_pckd)
            lapack.ormqr(QA, tauA, x, side = 'L', trans = 'T')
            print "7"
            # y := x[:p]
            #    = Q1' * (bx + Gs' * W^{-T} * bz)
            blas.copy(y, yy)
            blas.copy(x, y, n = p)
            print "8"
            # x[:p] := v = R^{-T} * by
            blas.copy(yy, x)
            lapack.trtrs(QA, x, uplo = 'U', trans = 'T', n = p)
            print "9" # Fails here because R not full rank.
            # x[p:] := K22^{-1} * (x[p:] - K21*x[:p])
            #        = K22^{-1} * (Q2' * (bx + Gs' * W^{-T} * bz) - K21*v)
            blas.gemv(K, x, x, alpha = -1.0, beta = 1.0, m = n-p, n = p,
                offsetA = p, offsety = p)
            lapack.potrs(K, x, n = n-p, offsetA = p*(n+1), offsetB = p)
            print "10"
            # y := y - [K11, K12] * x
            #    = Q1' * (bx + Gs' * W^{-T} * bz) - K11*v - K12*w
            blas.gemv(K, x, y, alpha = -1.0, beta = 1.0, m = p, n = n)

            # y := R^{-1}*y
            #    = R^{-1} * (Q1' * (bx + Gs' * W^{-T} * bz) - K11*v
            #      - K12*w)
            lapack.trtrs(QA, y, uplo = 'U', n = p)

            # x := [Q1, Q2] * x
            lapack.ormqr(QA, tauA, x, side = 'L')
            print "13"
            # bzp := Gs * x - bzp.
            #      = W^{-T} * ( GG*ux - bz ) in packed storage.
            # Unpack and copy to z.
            blas.gemv(Gs, x, bzp, alpha = 1.0, beta = -1.0, m = cdim_pckd)
            unpack(bzp, z, dims, mnl)
            print "14"

        return solve

    return factor
