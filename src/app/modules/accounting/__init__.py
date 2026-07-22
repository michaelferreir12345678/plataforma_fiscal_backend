"""Módulo Patrimônio (DCA) & Explorador MSC — Módulo 11 (Sprint 12).

Materializa o Plano de Contas Aplicado ao Setor Público (PCASP) como dimensão
hierárquica (``gold.dim_conta_pcasp``, ltree), os saldos mensais da Matriz de Saldos
Contábeis (``gold.fato_msc_saldo``, particionada por ``uf``/``ano``), o rollup pré-calculado
para o explorador com drill-down *lazy* (``gold.mart_msc_rollup``) e os balanços da
Declaração de Contas Anuais (``gold.fato_balanco``).
"""
