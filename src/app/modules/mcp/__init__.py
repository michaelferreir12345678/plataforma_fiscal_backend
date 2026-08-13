"""Exposição MCP da camada de ferramentas (Sprint IA-3).

Este pacote é **adaptador**, não domínio. Ele autentica um cliente externo, traduz JSON-RPC
para uma chamada de ``shared/tooling`` e devolve o resultado — nada mais. Toda garantia
(escopo, licença, ``as_of``, ``source_ref``, auditoria) mora dentro do envelope da
ferramenta, e é por isso que trocar MCP por outro protocolo amanhã não move uma linha de
regra fiscal (§2.2 do plano).

A regra que este pacote não pode violar, e que os testes vigiam: **zero regra de negócio
aqui**. Se algum dia for preciso decidir aqui dentro quem pode ver qual ente, o desenho
quebrou — é o achado A22 da Sprint E1 renascendo numa porta nova.
"""
