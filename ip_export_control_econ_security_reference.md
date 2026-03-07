# IP × Export Control × Economic Security: Strategic Intelligence Reference
# 知的財産 × 輸出管理 × 経済安全保障：統合戦略インテリジェンス・リファレンス

> **Purpose / 目的**: This document serves as a high-level philosophical and contextual reference for an integrated compliance and technology governance application. It is NOT a functional specification. It encodes the "why" — the geopolitical, regulatory, and strategic reasoning — that should inform every layer of the system's design, from data models to user-facing decisions.
>
> **このドキュメントはアプリの機能仕様ではない。** アプリケーション全体の設計思想・文脈・判断基準を形成する「なぜそうあるべきか」を定義するハイレベル・リファレンスである。データモデルからUI上の意思決定支援まで、すべてのレイヤーがこの思想を前提とすべきである。

---

## 1. Foundational Thesis: The Convergence Era / 基本テーゼ：収斂の時代

### 1.1 IP, Export Controls, and Investment Regulation Are Now One System

The traditional separation of intellectual property management, export control compliance, and foreign investment screening is obsolete. In the 2020s, these three domains have converged into a single "economic security governance" framework across all major jurisdictions.

**日本語要約**: 知的財産管理・輸出管理・対外投資規制は、かつて別々のコンプライアンスだったが、2020年代に「経済安全保障ガバナンス」として一体化した。このシステムはこの収斂を前提に設計されなければならない。

**Key evidence:**
- Japan's Economic Security Promotion Act (ESPA, 2022) bundles critical supply chains, critical infrastructure, critical technology support, and patent application non-disclosure into a single legislative framework.
- The US simultaneously deploys export controls (BIS/EAR), outbound investment screening (OISP), CHIPS Act guardrails, and CFIUS reviews as a coordinated toolkit targeting the same technology sectors (semiconductors, AI, quantum).
- The EU's Economic Security Strategy (2023) explicitly links dual-use export controls (Regulation 2021/821), FDI screening, and outbound investment monitoring into one "de-risking" approach.

**Implication for system design**: Any compliance determination, risk scoring, or advisory output must consider all three dimensions simultaneously. A product classified as non-listed under export control may still trigger outbound investment restrictions or patent non-disclosure requirements. Siloed assessments are by definition incomplete.

### 1.2 Technology Lifecycle as the Unit of Analysis

The correct unit of analysis is NOT a single transaction, shipment, or patent application. It is the **technology lifecycle**: from R&D inception → patent filing → prototype → manufacturing → export/licensing → joint venture → end-use.

Each stage of this lifecycle intersects with different regulatory regimes:
- **R&D phase**: Deemed export controls (FEFTA Art. 25; EAR §734.13), security clearance requirements, trade secret protection
- **Patent filing**: Foreign filing license requirements (USPTO), patent non-disclosure screening (Japan ESPA), invention secrecy orders (US Invention Secrecy Act)
- **Manufacturing/procurement**: CHIPS Act guardrails, supply chain due diligence, critical mineral sourcing restrictions
- **Export/licensing**: List-based and catch-all controls, end-use/end-user screening, sanctions compliance
- **Joint venture/M&A**: CFIUS/FDI screening, OISP outbound investment review, technology clawback provisions
- **Standards/SEPs**: FRAND commitments vs. national security considerations, ITC public interest factors

**Implication for system design**: The system should be capable of mapping a technology asset across its entire lifecycle and surfacing relevant regulatory touchpoints at each stage. This is fundamentally different from point-in-time compliance checking.

---

## 2. Regulatory Intelligence Layer: What the System Must "Know" / 規制インテリジェンス層

### 2.1 Japan's Economic Security Architecture (日本の経済安全保障体制)

**Four Pillars of ESPA (経済安全保障推進法の4本柱):**
1. **Supply Chain Resilience (サプライチェーン強靱化)**: Critical material designation, supply chain surveys, stockpile requirements
2. **Critical Infrastructure Protection (基幹インフラ安全性確保)**: Prior review of systems/equipment procurement for 14 infrastructure sectors
3. **Critical Technology Support (先端的な重要技術の開発支援)**: Government-funded R&D programs with technology protection obligations
4. **Patent Non-Disclosure (特許出願非公開)**: Screening of patent applications in security-sensitive technology areas, with potential preservation orders that prohibit foreign filing, restrict disclosure, and suspend publication

**Security Clearance System (セキュリティ・クリアランス制度):**
- Enacted May 2024 as the "Act on the Protection and Utilization of Important Economic Security Information"
- Enforced from May 17, 2025
- Covers 19 categories of "Critical Economic Security Information" (重要経済安保情報)
- Applies to government personnel AND private sector individuals handling designated information
- Violations: imprisonment up to 5 years
- Cross-border sharing: must go through government-to-government channels
- Japan was the last G7 nation without such a system; its introduction is designed to enable information sharing with allies

**December 2025 revision**: The government plans to bring highly confidential economic security information under the scope of the Specially Designated Secrets law, creating a two-tier classification system and streamlining clearance evaluations.

**Deemed Export Control Clarification (みなし輸出管理の明確化):**
- Amended May 2022: Technology transfer to a "resident" under the significant influence of a "non-resident" is now controlled
- Three "Specific Categories" define when influence is presumed:
  - Category 1: Dual employment with unaffiliated foreign entity
  - Category 2: Under contractual obligation to act per foreign entity's instructions on technology use
  - Category 3: Spouse or close family member is a non-resident from a "specified country" AND the individual has lived in Japan for less than 10 years (with conditions)
- This directly impacts joint R&D, university-industry collaboration, and foreign employee onboarding

**Trade Secret Management (営業秘密管理):**
- 2025 revision of Management Guidelines for Trade Secrets
- AIST case (2023): researcher convicted for sending fluorinated compound data to Chinese company — underscores criminal liability for technology leakage from research institutions
- Key standard: "managed as secret, useful for business, not publicly known" (Unfair Competition Prevention Act)

**METI Action Plan (産業・技術基盤強化行動計画):**
- Revised May 2025: scope expanded from cutting-edge fields to "entire range of industrial and technological basis"
- Now covers generative AI, quantum, steel, shipbuilding, outer space, oceans, energy
- Strategic objective: ensure Japan's "autonomy and indispensability" (自律性と不可欠性)

**CISTEC Role:**
- Central intermediary for export control implementation in Japan
- Provides: commodity classification tools, parameter sheets, model ICP, academic guidance, international outreach
- University/research institution guidelines for technology security management

### 2.2 United States Regulatory Architecture

**BIS/EAR Export Controls:**
- October 2022 & October 2023 rules: Advanced computing chips controlled by total processing performance (TPP) and performance density thresholds; expanded geographic scope (43+ countries); new "U.S. person" activity restrictions
- Design data (GDSII) of advanced chips treated as controlled technology; foundries must conduct due diligence on designs exceeding 50 billion transistors with HBM
- Semiconductor manufacturing equipment controls aligned with Japan and Netherlands

**Outbound Investment Security Program (OISP):**
- Final rule effective January 2, 2025
- Covers: semiconductors, AI, quantum technologies
- Targets: China (incl. Hong Kong, Macau)
- Two categories: prohibited transactions (most sensitive) and notifiable transactions
- Applies to U.S. persons' investments including greenfield, JV, LP commitments, convertible debt

**CHIPS Act Guardrails:**
- 10-year restriction on material expansion of semiconductor manufacturing in "foreign countries of concern" (China, Russia, Iran, North Korea)
- Technology Clawback: prohibits joint research or technology licensing with foreign entities of concern in national security-sensitive areas
- Recipients must report significant transactions; violations trigger full clawback of federal incentives ($39B+ program)
- Legacy semiconductor exception exists but is narrowly defined

**Patent-Export Control Intersection:**
- Foreign filing license (35 USC §184): Filing a US-origin invention abroad without USPTO license is a federal offense; license is separate from and does not override EAR requirements
- Pre-publication patent applications contain unpublished technical data that may be EAR-controlled; sharing with foreign persons (including via patent prosecution) may constitute "deemed export"
- BIS has delegated limited authority to USPTO for patent-related technology transfers, but EAR still applies independently
- Invention Secrecy Act: thousands of invention secrecy orders active; prevents patent publication and imposes use restrictions

**IP and National Security:**
- CSIS Transition Report (March 2025): argues strong IP rights are a pillar of national security; recommends revitalizing patent system, R&D tax incentives, and coherent IP enforcement as economic security tools
- Standard Essential Patents (SEPs): ITC considers national security as a "public interest" factor in patent exclusion orders, particularly for 5G infrastructure

### 2.3 European Union

**Dual-Use Regulation 2021/821:**
- Expanded scope: "technical assistance" as independent control category; cyber-surveillance items with human rights considerations
- New general export authorizations: EU007 (intra-group transfers), EU008 (encryption)
- ICP requirement for certain general authorizations
- 2024 & 2025 updates aligned with Wassenaar Arrangement decisions: added semiconductor manufacturing/test equipment, ALD equipment, EUV pellicles, programmable logic devices, 3D printing metal powders, peptide synthesizers

**Outbound Investment:**
- January 2025 Commission Recommendation: asks member states to review 2021-present outbound investments in semiconductors, AI, quantum
- Non-binding but establishes data collection framework; widely interpreted as precursor to binding regulation
- Netherlands already implemented additional semiconductor equipment export controls (ASML DUV restrictions)

### 2.4 China's Counter-Measures

**Critical Mineral Export Controls:**
- July 2023: gallium, germanium licensing
- August 2024: antimony, superhard materials
- December 2024: banned export of gallium, germanium, antimony, superhard materials to the US; stricter graphite end-use reviews
- April 2025: 7 rare earth elements + permanent magnets (presumed denial to US defense)
- October 2025: expanded to wider RE elements + processing equipment + extraterritorial de minimis rule (0.1% by value threshold)
- November 2025: one-year pause on some measures under US-China trade talks, but April 2025 controls remain active
- **De minimis rule**: If a product manufactured OUTSIDE China contains ≥0.1% (by value) of specified China-origin rare earth metals/alloys/oxides, it is subject to Chinese export controls. This has profound implications for global supply chains.

**Export Control Law (2020):**
- Dual-use items, military items, nuclear, and "other goods/technologies/services" related to national security
- Extraterritorial provisions: applies to re-exports of China-origin items
- Retaliatory mechanism: banning exports to specific entities or for military end-use

---

## 3. Strategic Intelligence: What the Data Tells Us / 戦略インテリジェンス

### 3.1 Export Controls Accelerate Adversary Innovation

A critical and counterintuitive finding that the system's advisory logic must internalize:

**Harvard Business School Working Paper (2024/2025)**: Study of the 2007 US "China Military Catch-All Rule" found:
- Treated Chinese firms reduced imports of controlled products (as intended)
- BUT increased R&D spending by 49.1%, patent applications by 41.3%, active inventors by 30.4%
- Patent applications in controlled technology domains rose 65.1%; patents in OTHER domains rose 41.6%
- Domestic SUPPLIERS of controlled goods quadrupled their related patent applications
- Response was concentrated in non-state-owned firms and grew over time

**CSIS Analysis (May 2024)**: Study of 30 leading semiconductor firms found:
- No substantial evidence that US October 2022 controls harmed innovation at US/allied firms
- Impacted companies increased R&D spending 68% vs. 27% for non-impacted peers
- This may be attributable to AI semiconductor demand boom benefiting exactly the companies most exposed to controls

**Implication for system design**: Risk assessments should not assume that export controls permanently deny adversary access. The system should model adversary adaptation timelines and indigenous substitution probability. "Technology half-life under export control" could be a useful metric — how long before a controlled capability is independently replicated in a restricted jurisdiction.

### 3.2 Technology Sovereignty as a Competitive Dynamic

**Bain Technology Report (2025)**: Key cutting-edge domains (semiconductors, AI, communications, quantum, biotech) are now "conduits for countries' political power, national security, and strategic advantage." Governments are actively directing capital, talent, and IP flows.

**WEF (January 2026)**: "In a hyper-connected digital world, no nation is truly sovereign." Organizations must "identify potential risks and design an affordable sovereignty strategy that enables them to protect their businesses" while ensuring "sovereignty becomes a stimulus, not a constraint, for innovation."

**McKinsey (December 2025)**: "The most successful sovereign AI strategies will combine local control with global collaboration."

**Implication for system design**: The system must help users navigate the paradox of sovereignty — achieving strategic control without isolationism. Advisory outputs should distinguish between:
- Technologies where sovereignty (full domestic control) is strategically necessary
- Technologies where interdependence with trusted allies is acceptable and efficient
- Technologies where global openness remains the best innovation strategy

### 3.3 The Patent-Geopolitics Nexus

**Sino-US S&T Frictions and Patent Flows (2025 preprint)**: Machine learning analysis of cross-national patent data shows:
- US-China S&T frictions have significantly reduced cross-border knowledge flows (measured by patent citations and co-inventions)
- Impact is most severe in technology areas reliant on basic scientific research where US strength is concentrated
- This represents a measurable "knowledge fragmentation" with long-term innovation cost

**Patent Analytics as Strategic Intelligence:**
- Patent filing patterns serve as leading indicators of technology investment direction
- Patent citation networks reveal dependency structures and bottleneck technologies
- IPC/CPC classification mapping to export control lists can identify technologies likely to face future regulation
- Corporate patent portfolio analysis can reveal covert military-civil fusion strategies

**Implication for system design**: Patent data is not just an IP management input — it is a strategic intelligence asset. The system should:
- Cross-reference patent classifications (IPC/CPC) with export control classification numbers (ECCN, 外為法リスト番号)
- Map patent ownership networks to identify dependencies on entities in restricted jurisdictions
- Track patent filing trend changes as early-warning signals for regulatory shifts
- Identify "bottleneck patents" — IP where the owner occupies an irreplaceable position in a regulated supply chain

---

## 4. Consortium & Partnership Governance / コンソーシアム・パートナーシップ・ガバナンス

### 4.1 The Coopetition Imperative

In technology sectors where development costs exceed any single firm's capacity (advanced semiconductors, quantum computing, 6G), international consortia are the only viable innovation model. However, every consortium now operates within a web of export controls, investment restrictions, and patent governance rules.

**Rapidus-IBM-imec Model:**
- Rapidus (Japan) + IBM (US) for 2nm process technology + chiplet packaging
- imec (Belgium) for advanced logic R&D
- Funded through NEDO; engineers co-located at IBM facilities in North America
- EUV equipment sourced from ASML (Netherlands) — directly impacted by US-Japan-Netherlands export control alignment
- This consortium is a prototype for "allied technology bloc" collaboration

**Patent Pool Governance Lessons:**
- Patent pools (e.g., MPEG-LA, Avanci, UTLP) have established governance principles applicable to security-sensitive consortia
- Key safeguards: complementary-only patents (no substitutes), independent licensing rights, open admission, grant-back limitations
- DOJ/FTC antitrust guidance consistently favors pools with these safeguards
- Academic research confirms pools increase patenting rates among members, promoting pro-competitive innovation
- Critical adaptation needed: in export-control-sensitive domains, pool governance must additionally address:
  - Member nationality/jurisdiction screening
  - Technology tiering (what can be shared across all members vs. allied-only vs. national-only)
  - Regulatory change triggers (what happens if a member is sanctioned or a technology is re-classified)
  - Dispute resolution when geopolitical events create conflicting obligations

### 4.2 Academic-Industry Collaboration Risks

**Japan Government Guidance:**
- METI "Guidance for the Control of Sensitive Technologies for Security Export" (updated 2025): multi-layered approach requiring technology classification BEFORE research begins, not as an afterthought
- Cabinet Office guidelines on university-foreign company collaboration: require export control clauses in joint research contracts, undertaking letters from foreign partners, compliance with deemed export for foreign students/researchers
- AIST conviction case: established precedent that research institutions are accountable for trade secret/technology leakage; criminal penalties apply

**Model for System Integration:**
- Pre-research screening: Is the proposed research topic export-controlled? Does it involve deemed exports? Would resulting inventions be subject to patent non-disclosure?
- During research: Access controls, information compartmentalization, regular compliance check-ins
- Post-research: Patent filing strategy (domestic first? foreign filing license? non-disclosure risk?), publication review, technology transfer assessment

---

## 5. The Four-Quadrant Technology Strategy / 技術4象限戦略

### 5.1 Framework

Every technology in a company's portfolio should be mapped onto two axes:
- **X-axis: Technology Sovereignty Value (技術主権価値)** — How critical is this technology to national/corporate strategic autonomy? (Measured by: patent strength, market share concentration, substitutability, government designation as "critical")
- **Y-axis: Regulatory Sensitivity (規制感度)** — How extensively is this technology subject to export controls, investment restrictions, or patent non-disclosure? (Measured by: presence on control lists, OISP coverage, ESPA designation, catch-all risk)

**Quadrant I (High Sovereignty × High Regulation): "Fortress Technologies" (要塞技術)**
- Examples: EUV lithography, advanced logic process IP, quantum cryptography, certain AI training algorithms
- Strategy: Maximum protection — patent non-disclosure consideration, allied-only consortia, strict technology compartmentalization, domestic manufacturing priority
- Risk: Over-protection stifles innovation; must maintain sufficient openness to attract talent and capital

**Quadrant II (High Sovereignty × Low Regulation): "Crown Jewels at Risk" (無防備な至宝)**
- Examples: Advanced materials science, next-gen battery chemistry, certain biotech platforms
- Strategy: Proactive IP filing + trade secret layering; monitor for regulatory escalation; establish FRAND positions before standardization locks in
- Risk: These technologies may rapidly shift to Quadrant I as regulators catch up (e.g., AI before 2022, gallium/germanium before 2023)

**Quadrant III (Low Sovereignty × High Regulation): "Compliance Burden" (コンプライアンス負荷)**
- Examples: Legacy semiconductors, certain chemicals, dual-use equipment where alternatives exist
- Strategy: Efficient compliance automation; consider strategic exit or licensing; use as bargaining chip in consortium negotiations
- Risk: Over-investment in compliance for commoditized technologies; regulatory arbitrage by competitors

**Quadrant IV (Low Sovereignty × Low Regulation): "Open Field" (開放領域)**
- Examples: Consumer electronics, standard industrial components, widely available software
- Strategy: Maximize global reach; open licensing; leverage scale and speed
- Risk: Minimal from a technology security perspective, but supply chain disruption remains possible

### 5.2 Dynamic Monitoring

Technologies migrate between quadrants. The system must:
- Track regulatory proposals and multilateral regime updates (Wassenaar, MTCR, AG, NSG) that signal impending classification changes
- Monitor patent filing trends that indicate a technology is gaining sovereignty value
- Detect supply chain concentration changes that alter substitutability assessments
- Flag "Quadrant II → Quadrant I migration candidates" as strategic priorities requiring preemptive action

---

## 6. Corporate Governance Architecture / 企業ガバナンス・アーキテクチャ

### 6.1 Integrated Technology Security Committee (技術安全保障委員会)

The convergence of IP, export control, and investment regulation demands a single governance body that can make cross-cutting decisions:

**Composition:**
- Chair: CTO or dedicated Chief Technology Security Officer
- Members: Head of IP/Patents, Head of Export Control/Trade Compliance, Head of Legal/M&A, Head of R&D Strategy, CISO (Cybersecurity), Head of Government Affairs
- Advisory: External counsel (trade law + IP law), industry body liaison (CISTEC, relevant trade associations)

**Decision Authority:**
- Technology classification and quadrant assignment
- Patent filing strategy for security-sensitive inventions (file, non-disclose, or trade secret?)
- Consortium participation approval (geopolitical risk assessment)
- Joint research contract terms (export control clauses, IP allocation, exit provisions)
- M&A technology due diligence sign-off
- Incident response (technology leakage, sanctions designation, regulatory change)

### 6.2 Compliance as Competitive Advantage

The system should help reframe compliance not as cost but as competitive positioning:
- Companies with mature, auditable compliance programs are preferred partners in allied-nation consortia
- CHIPS Act eligibility requires demonstrable compliance infrastructure
- Security clearance of key personnel enables access to classified briefings and government R&D programs
- Strong trade secret management reduces insurance premiums and improves investor confidence

As FTI Consulting notes (January 2026): "Properly managing export controls becomes a differentiator because these compliance frameworks provide guidance for how a company should respond to a specific incident and offer a solid foundation for enterprise-wide risk management."

---

## 7. Data Architecture Implications / データアーキテクチャへの示唆

### 7.1 Entities the System Must Model

The system's data model should be capable of representing at minimum:

| Entity | Key Attributes | Regulatory Relevance |
|--------|---------------|---------------------|
| Technology/Invention | IPC/CPC class, TRL, description, related patents | Export control classification, non-disclosure screening, sovereignty quadrant |
| Patent Asset | Filing jurisdiction, publication status, inventors, assignees, claims | Foreign filing license status, non-disclosure risk, SEP status |
| Product/Component | HS code, ECCN, specifications, BOM | List-based and catch-all control, sanctions screening, critical mineral content |
| Business Partner | Jurisdiction, ownership chain, beneficial owners, affiliations | Entity List/SDN/DPL screening, OISP applicability, 50% rule (BIS affiliate) |
| Transaction | Type (export, transfer, license, investment), destination, end-use, value | Specific control requirements, general license eligibility, reporting obligations |
| Consortium/JV | Members, governance structure, IP allocation, technology scope | Multi-jurisdictional compliance, clawback risk, antitrust clearance |
| Person (researcher/employee) | Nationality, residency, dual affiliations, clearance status | Deemed export, specific categories screening, security clearance eligibility |

### 7.2 Cross-Reference Logic

The system's core intelligence derives from cross-referencing:
- **Patent IPC/CPC ↔ Export Control Lists**: Map IPC subclasses to ECCN categories and 外為法別表 items. This enables early identification of patents that may face non-disclosure or that cover export-controlled technologies.
- **Patent Assignee/Inventor ↔ Restricted Party Lists**: Screen patent co-inventors and assignees against BIS Entity List, OFAC SDN, Japan End-User List, EU sanctions lists.
- **HS Code ↔ ECCN ↔ CPC**: Link product classifications to technology classifications to patent classifications, creating a unified "technology-product-IP" map.
- **Ownership Chain ↔ OISP/CFIUS Thresholds**: Map corporate ownership structures to determine if a transaction triggers outbound investment review.
- **Supply Chain Node ↔ Critical Mineral/Technology Dependence**: Identify where a company's supply chain depends on technologies or materials subject to foreign export controls (e.g., China's RE de minimis rule).

### 7.3 Advisory Output Philosophy

The system should NOT simply output "compliant/non-compliant." It should provide:

1. **Situational Awareness**: What regulatory regimes apply to this technology/transaction/partnership, and how are they evolving?
2. **Risk Stratification**: Where does this fall on the four-quadrant map? What is the migration trajectory?
3. **Scenario Planning**: Under Scenario A (status quo), Scenario B (regulatory escalation), Scenario C (détente), what are the compliance and strategic implications?
4. **Actionable Recommendations**: Specific steps (file patent domestically first, obtain foreign filing license, restructure JV ownership, diversify supply source) with regulatory basis cited.
5. **C-Level Narrative**: A synthesized, jargon-minimized summary suitable for CEO/COO/CTO decision-making — not just legal risk, but strategic opportunity.

---

## 8. Geopolitical Scenario Framework / 地政学シナリオ・フレームワーク

### 8.1 Three Scenarios for Technology Governance

**Scenario A: "Allied Technology Bloc" (同盟国技術ブロック)**
- US-Japan-EU-Korea-Australia technology alignment deepens
- Harmonized export controls, mutual recognition of security clearances, coordinated outbound investment rules
- Japan benefits from trusted partner status; full participation in CHIPS-like programs
- Risk: bloc becomes exclusive; emerging market access narrows

**Scenario B: "Competitive Coexistence" (競争的共存)**
- Partial decoupling: controls on frontier technologies, but continued trade in mature technologies
- "Small yard, high fence" approach persists; fence definition shifts over time
- Most likely near-term scenario
- Risk: regulatory complexity maximizes compliance burden; gray zones expand

**Scenario C: "Digital/Technology Decoupling" (デジタル・技術デカップリング)**
- Full bifurcation of technology ecosystems (US-led vs. China-led)
- Parallel standards, separate patent systems, independent supply chains
- Maximum security but maximum cost and innovation fragmentation
- Risk: Japan caught between blocs; forced to choose between market access and alliance obligations

**Implication for system design**: The system should allow users to stress-test their technology portfolio and partnership decisions against all three scenarios. "Under Scenario C, does our supply chain survive? Under Scenario A, which new consortium opportunities open up?"

---

## 9. Key Reference Sources / 主要参照ソース

### Government & Multilateral
- Japan: Economic Security Promotion Act (経済安全保障推進法), Foreign Exchange and Foreign Trade Act (外為法), METI Action Plan, Cabinet Office Patent Non-Disclosure Guidelines, CISTEC resources
- US: EAR (15 CFR Parts 730-774), OFAC regulations, CHIPS Act and NIST Guardrails Rule, Treasury OISP Final Rule, USPTO Export Control guidance
- EU: Regulation 2021/821 (Dual-Use), FDI Screening Regulation, Commission Outbound Investment Recommendation (Jan 2025)
- Multilateral: Wassenaar Arrangement, MTCR, Australia Group, NSG (annual list updates)
- China: Export Control Law (2020), Rare Earth Management Regulations (2024), MOFCOM export ban announcements

### Think Tanks & Research
- CSIS: "Protecting Intellectual Property for National Security" (Gupta, Iancu et al., March 2025); "Did US Semiconductor Export Controls Harm Innovation?" (May 2024)
- Harvard Business School: "Export Controls and Innovation in Sanctioned Countries" (Liu et al., WP 25-004, 2024/2025)
- Bain & Company: "Sovereign Tech, Fragmented World" (Technology Report 2025)
- WEF: "Why the Race for Tech Sovereignty Is a Balancing Act" (January 2026)
- McKinsey: "The Sovereign AI Agenda" (December 2025)
- Swedish Institute of International Affairs: "Controlling Critical Technology in an Age of Geoeconomics" (UI Report No.1, 2023)
- ITIF: "Decoupling Risks: How Semiconductor Export Controls Could Harm Innovation" (2025)
- Georgetown CSET: BIS export control explainers, China MOFCOM translation series

### Legal & Practitioner
- FTI Consulting: "Emerging Risk: Export Controls Compliance in New Technologies" (January 2026)
- Gibson Dunn: "International Trade 2024 Year-End Update"
- BIS Export Compliance Toolkit (8 Elements of an Effective ECP)
- WIPO: Patent Pools Report (competition and innovation implications)
- DOJ/FTC: Antitrust Guidelines for the Licensing of Intellectual Property (2017); Avanci and UTLP business review letters
- Crowell & Moring, IPWatchdog: US patent prosecution and export control intersection analysis

### Patent Intelligence Platforms
- LexisNexis IP (PatentSight): Corporate patent mapping, portfolio analytics
- Patsnap: AI-driven patent analytics, 202M+ patents across 174 jurisdictions
- Patlytics: Patent-to-product mapping, portfolio valuation
- Lens.org: Open patent and scholarly data integration

---

## 10. Design Principles Summary / 設計原則サマリー

1. **Convergence-First (収斂優先)**: Every assessment considers IP, export control, investment regulation, and sanctions simultaneously.

2. **Lifecycle-Oriented (ライフサイクル志向)**: Track technologies from R&D to end-of-life, surfacing regulatory touchpoints at each stage.

3. **Quadrant-Mapped (4象限マッピング)**: Classify all technology assets by sovereignty value × regulatory sensitivity; monitor migration.

4. **Scenario-Resilient (シナリオ耐性)**: Enable stress-testing against allied bloc, competitive coexistence, and full decoupling scenarios.

5. **Cross-Reference-Powered (クロスリファレンス駆動)**: Core intelligence comes from mapping patent data ↔ export control lists ↔ restricted parties ↔ supply chain dependencies.

6. **C-Level-Readable (Cレベル可読性)**: All outputs must have a synthesized layer suitable for CEO/COO/CTO decision-making, not just compliance officers.

7. **Adversary-Aware (相手方適応意識)**: Model the possibility that export controls accelerate adversary innovation; factor adversary adaptation timelines into risk assessments.

8. **Compliance-as-Advantage (コンプライアンス=競争優位)**: Frame compliance infrastructure as a qualification for consortium participation, government program access, and trusted partner status.

9. **Dynamic Monitoring (動的モニタリング)**: Continuously track regulatory proposals, patent filing trends, supply chain shifts, and geopolitical indicators as inputs to technology governance decisions.

10. **Bilingual-Native (日英ネイティブ)**: All key concepts, legal terms, and regulatory references must be accessible in both Japanese and English, reflecting the operational reality of Japanese multinational enterprises.

---

*Last updated: 2026-03-07*
*Version: 2.0*
*Maintainer: Takehiro Sato — Trade Compliance & IP Strategy*
